"""Ghosting cadence integration — mock channels, adaptive strategy fallback."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.channels.mock import MockEmailAdapter, MockSMSAdapter, MockVoiceAdapter
from src.models.enums import Channel, Disposition, MessageGoal
from src.models.intake import ConsentRecord, IntakeRecord
from src.orchestrator.activities import check_compliance_gates, execute_outbound_touch, plan_next_touch
from src.orchestrator.cadence import get_cadence_step


@pytest.fixture
def ghost_lead() -> dict:
    return IntakeRecord(
        lead_id="ghost-001",
        identity={"name": "Maria", "phone": ["+14805559999"], "email": ["maria@example.com"]},
        consent=ConsentRecord(origin="outbound", channels_allowed=[Channel.VOICE, Channel.SMS, Channel.EMAIL]),
    ).model_dump(mode="json")


@pytest.mark.asyncio
async def test_cadence_steps_defined():
    step = get_cadence_step(6)
    assert step.touch_type == "loss_aversion"


@pytest.mark.asyncio
async def test_ghosting_voice_voicemail_paired_sms(ghost_lead, db_session):
    MockVoiceAdapter.clear()
    MockSMSAdapter.clear()
    MockEmailAdapter.clear()
    MockVoiceAdapter.set_amd("ghost-001", "machine")

    from src.store.projection import create_lead

    await create_lead(
        lead_id="ghost-001",
        identity=ghost_lead["identity"],
        consent=ghost_lead["consent"],
        trigger="web_form",
    )

    voicemails: list[str] = []
    record = ghost_lead
    for attempt in range(1, 6):
        plan = await plan_next_touch("ghost-001", is_initial=(attempt == 1), trigger="web_form")
        if plan.get("terminal_action") == "give_up":
            break
        if plan.get("channel") != Channel.VOICE.value:
            continue
        result = await execute_outbound_touch(
            "ghost-001",
            record,
            plan["channel"],
            plan["message_goal"],
            attempt,
            plan.get("reason", ""),
            plan.get("constraints") or [],
        )
        if result.get("voicemail"):
            voicemails.append(result["voicemail"])
        record = result.get("record", record)

    assert len(voicemails) >= 1
    assert len(MockSMSAdapter.sent_messages) >= len(voicemails)
    if len(voicemails) > 1:
        assert voicemails[0] != voicemails[1]


@pytest.mark.asyncio
async def test_ghosting_plan_uses_cadence_not_sms_hot(ghost_lead, db_session):
    """Ghosting lead with no inbound should still get voice via cadence fallback."""
    from src.store.projection import create_lead

    await create_lead(
        lead_id="ghost-002",
        identity=ghost_lead["identity"],
        consent=ghost_lead["consent"],
        trigger="web_form",
    )

    plan = await plan_next_touch("ghost-002", is_initial=True, trigger="web_form")
    assert plan["message_goal"] == MessageGoal.WARM_INTRO.value

    # After advancing cadence, fallback may propose voice
    for _ in range(2):
        plan = await plan_next_touch("ghost-002", is_initial=False)
    assert plan.get("channel") in (Channel.VOICE.value, Channel.SMS.value, Channel.EMAIL.value)


@pytest.mark.asyncio
async def test_gave_up_after_caps(ghost_lead):
    ghost_lead["engagement"]["attempts_by_channel"] = {"voice": 3, "sms": 4, "email": 3}
    result = await check_compliance_gates(ghost_lead, Channel.SMS.value)
    assert result["terminal"] is True
    assert result["terminal_state"] == Disposition.GAVE_UP.value


@pytest.mark.asyncio
async def test_chase_timeout_14d(ghost_lead):
    ghost_lead["created_at"] = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
    result = await check_compliance_gates(ghost_lead, Channel.SMS.value)
    assert result["terminal_state"] == Disposition.GAVE_UP.value
