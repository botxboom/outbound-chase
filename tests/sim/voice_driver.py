"""Simulated multi-turn voice driver — no PSTN required."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.api.vapi_llm import _generate_dynamic_opener
from src.brain.agent import ConversationBrain
from src.config import settings
from src.models.intake import IntakeRecord


@dataclass
class TranscriptTurn:
    role: str
    content: str
    annotations: list[str] = field(default_factory=list)
    brain_result: dict[str, Any] | None = None


@dataclass
class VoiceTranscript:
    lead_id: str
    turns: list[TranscriptTurn] = field(default_factory=list)
    final_record: dict[str, Any] | None = None

    def agent_texts(self) -> list[str]:
        return [t.content for t in self.turns if t.role == "assistant"]

    def all_tool_fields(self) -> list[str]:
        fields = []
        for t in self.turns:
            if t.brain_result:
                for slot in t.brain_result.get("slots", []):
                    fields.append(slot.get("field", ""))
        return fields


async def run_voice_conversation(
    intake_record: dict,
    caller_script: list[str],
    lead_id: str = "test-voice",
) -> VoiceTranscript:
    """Drive a multi-turn voice conversation using the real brain."""
    brain = ConversationBrain()
    transcript = VoiceTranscript(lead_id=lead_id)

    opener = _generate_dynamic_opener(intake_record)
    print(f"A: {opener}\n", flush=True)
    transcript.turns.append(
        TranscriptTurn(role="assistant", content=opener, annotations=["[EVENT] call.attempted", "[AMD] human"])
    )

    record = dict(intake_record)
    total = len(caller_script)
    for i, caller_msg in enumerate(caller_script):
        print(f"C: {caller_msg}", flush=True)
        print(
            f"  [LLM] Turn {i + 1}/{total} — calling {settings.llm_model} "
            f"(timeout {settings.llm_timeout_seconds:.0f}s)...",
            flush=True,
        )

        result = await brain.run_turn(
            intake_record=record,
            channel="voice",
            lead_id=lead_id,
            inbound_message=caller_msg,
        )

        annotations = []
        for slot in result.get("slots", []):
            annotations.append(f"[TOOL] update_slot({slot.get('field')}=...)")
        if result.get("action") == "stop":
            annotations.append("[STATE] disposition=OPTED_OUT")
        if result.get("action") == "wrong_number":
            annotations.append("[STATE] disposition=WRONG_NUMBER")
        if result.get("action") == "send_retainer":
            annotations.append("[TOOL] send_retainer")
        if result.get("action") == "escalate":
            annotations.append("[TOOL] escalate_to_human")

        agent_text = (result.get("content") or {}).get("text", "")
        if not agent_text:
            agent_text = "(no text — check LLM model / increase LLM_MAX_TOKENS)"
        print(f"A: {agent_text}", flush=True)
        for ann in annotations:
            print(f"    {ann}", flush=True)
        print(flush=True)

        transcript.turns.append(
            TranscriptTurn(role="user", content=caller_msg)
        )
        transcript.turns.append(
            TranscriptTurn(
                role="assistant",
                content=agent_text,
                annotations=annotations,
                brain_result=result,
            )
        )

        # Apply slots to in-memory record for next turn
        for slot in result.get("slots", []):
            field_name = slot.get("field")
            if field_name == "preferred_lang":
                record.setdefault("engagement", {})["language_observed"] = slot.get("value")
                continue
            record.setdefault("intake_slots", {})[field_name] = {
                "field": field_name,
                "value": slot.get("value"),
                "confidence": slot.get("confidence", 0.8),
            }

        if result.get("action") in ("stop", "wrong_number", "escalate"):
            break

    transcript.final_record = record
    return transcript
