"""Integration tests for adaptive strategy with DB."""
from __future__ import annotations

import pytest

from src.models.enums import Channel, MessageGoal, Responsiveness
from src.models.intake import ConsentRecord, IntakeRecord
from src.orchestrator.activities import extract_signals, plan_next_touch, process_inbound


@pytest.mark.asyncio
async def test_sms_inbound_plans_sms_not_voice(db_session):
    lead_id = "adapt-sms-001"
    from src.store.projection import create_lead

    await create_lead(
        lead_id=lead_id,
        identity={"name": "Maria", "phone": ["+14805559999"], "email": ["maria@example.com"]},
        consent={
            "origin": "inbound",
            "channels_allowed": ["voice", "sms", "email"],
            "ai_voice_consent": True,
        },
        trigger="web_form",
    )

    await process_inbound(
        lead_id,
        {"channel": "sms", "message": "Hi sorry I missed your call, yes accident last week"},
    )

    plan = await plan_next_touch(lead_id, is_initial=False)
    assert plan["channel"] == Channel.SMS.value
    assert plan["channel"] != Channel.VOICE.value
    assert plan.get("message_goal") in (
        MessageGoal.GAP_FILL.value,
        MessageGoal.FOLLOW_UP.value,
        MessageGoal.CONFIRM_SLOTS.value,
    )


@pytest.mark.asyncio
async def test_extract_signals_persists(db_session):
    lead_id = "adapt-sig-001"
    from src.store.projection import create_lead, load_lead_record

    await create_lead(
        lead_id=lead_id,
        identity={"name": "David", "phone": ["+14805551234"]},
        consent={"origin": "outbound", "channels_allowed": ["sms", "voice"], "ai_voice_consent": True},
    )

    await extract_signals(
        lead_id,
        {
            "direction": "inbound",
            "channel": "sms",
            "message": "Please text me don't call",
        },
    )

    record = await load_lead_record(lead_id)
    assert record is not None
    assert record.engagement.do_not_call is True
    assert record.engagement.responsiveness.get("sms") == Responsiveness.HOT.value


@pytest.mark.asyncio
async def test_initial_plan_inbound(db_session):
    lead_id = "adapt-inbound-001"
    from src.store.projection import create_lead

    await create_lead(
        lead_id=lead_id,
        identity={"name": "David", "phone": ["+14805551234"]},
        consent={"origin": "inbound", "channels_allowed": ["sms", "voice"], "ai_voice_consent": True},
        trigger="web_form",
    )

    plan = await plan_next_touch(lead_id, is_initial=True, trigger="web_form")
    assert plan["message_goal"] == MessageGoal.INSTANT_REPLY.value
    assert plan["channel"] == Channel.SMS.value
