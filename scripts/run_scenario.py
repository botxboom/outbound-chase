#!/usr/bin/env python3
"""Run design §7 scenarios with annotated output — no Vapi PSTN required."""
from __future__ import annotations

import argparse
import asyncio
import sys

from src.config import settings
from src.db.models import init_db
from src.models.enums import Channel
from src.models.intake import ConsentRecord, IntakeRecord
from src.orchestrator.activities import execute_outbound_touch, plan_next_touch, process_inbound
from src.store.projection import create_lead, load_lead_record
from tests.sim.caller_scripts import SCENARIOS
from tests.sim.voice_driver import run_voice_conversation


def _base_lead(name: str, lead_id: str, origin: str = "inbound") -> dict:
    return IntakeRecord(
        lead_id=lead_id,
        identity={"name": name, "phone": ["+14805551234"], "email": [f"{name.lower()}@example.com"]},
        consent=ConsentRecord(
            origin=origin,
            channels_allowed=[Channel.VOICE, Channel.SMS, Channel.EMAIL],
            ai_voice_consent=True,
            recording_consent=True,
        ),
    ).model_dump(mode="json")


def _print_transcript(transcript) -> None:
    for turn in transcript.turns:
        prefix = "A" if turn.role == "assistant" else "C"
        print(f"{prefix}: {turn.content}")
        for ann in turn.annotations:
            print(f"    {ann}")
    print()


async def run_voice_scenario(name: str) -> None:
    script = SCENARIOS.get(name)
    if not script:
        print(f"Unknown scenario: {name}")
        sys.exit(1)

    intake = _base_lead("David" if name != "spanish" else "Maria", f"cli-{name}")
    if name == "wrong_number":
        intake["identity"]["name"] = "Maria Lopez"

    print(f"=== Scenario: {name} (voice, simulated, LLM={settings.llm_model}) ===\n")
    await run_voice_conversation(intake, script, lead_id=f"cli-{name}")


async def run_sms_scenario(name: str) -> None:
    script = SCENARIOS.get(name, SCENARIOS["responsive"])
    lead_id = f"cli-sms-{name}"
    await create_lead(
        lead_id=lead_id,
        identity={"name": "Maria" if name == "spanish" else "David", "phone": ["+14805551234"], "email": ["test@example.com"]},
        consent={"origin": "inbound", "channels_allowed": ["sms", "voice", "email"], "ai_voice_consent": True},
    )

    print(f"=== Scenario: {name} (SMS, LLM={settings.llm_model}) ===\n")
    for msg in script:
        print(f"C: {msg}")
        result = await process_inbound(lead_id, {"channel": "sms", "message": msg, "event_type": "sms_inbound"})
        text = (result.get("content") or {}).get("text", "")
        if text:
            print(f"A: {text}")
        for slot in result.get("slots", []):
            print(f"    [TOOL] update_slot({slot.get('field')})")
        if result.get("action") == "stop":
            print("    [STATE] disposition=OPTED_OUT")
        print()

    record = await load_lead_record(lead_id)
    if record:
        print(f"[STATE] disposition={record.disposition.value}, slots={len(record.intake_slots)}")


async def run_ghosting() -> None:
    from src.channels.mock import MockVoiceAdapter

    lead_id = "cli-ghosting"
    MockVoiceAdapter.set_amd(lead_id, "machine")
    intake = _base_lead("Maria", lead_id, origin="outbound")
    await create_lead(lead_id=lead_id, identity=intake["identity"], consent=intake["consent"], trigger="web_form")

    print("=== Scenario: ghosting (adaptive strategy + cadence fallback, AMD=machine) ===\n")
    record = intake
    for attempt in range(1, 12):
        plan = await plan_next_touch(lead_id, is_initial=(attempt == 1), trigger="web_form")
        if plan.get("terminal_action") == "give_up":
            print(f"[EVENT] gave_up — {plan.get('reason', '')}")
            await execute_outbound_touch(
                lead_id,
                record,
                plan.get("channel", "sms"),
                plan.get("message_goal", "give_up"),
                attempt,
                plan.get("reason", ""),
                plan.get("constraints") or [],
            )
            break
        ch = plan.get("channel", "sms")
        goal = plan.get("message_goal", "follow_up")
        print(f"[PLAN] attempt={attempt} channel={ch} goal={goal} reason={plan.get('reason', '')}")
        result = await execute_outbound_touch(
            lead_id,
            record,
            ch,
            goal,
            attempt,
            plan.get("reason", ""),
            plan.get("constraints") or [],
        )
        if result.get("voicemail"):
            print(f"    [EVENT] call.voicemail_left: {result['voicemail'][:80]}...")
            print(f"    [EVENT] sms.outbound (paired): {result.get('paired_sms', '')[:60]}...")
        record = result.get("record", record)

    final = await load_lead_record(lead_id)
    if final:
        print(f"\n[STATE] attempts={final.engagement.attempts_by_channel}")
        print(f"[STATE] disposition={final.disposition.value}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Run Outbound-Chase scenarios")
    parser.add_argument(
        "scenario",
        choices=["legal_advice", "hostile", "responsive", "ghosting", "wrong_number", "spanish"],
    )
    parser.add_argument("--channel", choices=["voice", "sms"], default="voice")
    args = parser.parse_args()

    if args.scenario == "ghosting" or args.channel == "sms":
        await init_db()

    if args.scenario == "ghosting":
        await run_ghosting()
    elif args.channel == "sms":
        await run_sms_scenario(args.scenario)
    else:
        await run_voice_scenario(args.scenario)


if __name__ == "__main__":
    asyncio.run(main())
