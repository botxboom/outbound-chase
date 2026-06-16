"""Async SMS scenario tests — real LLM via process_inbound."""
from __future__ import annotations

import pytest

from src.models.enums import Disposition
from src.orchestrator.activities import process_inbound, send_retainer
from src.store.projection import create_lead, load_lead_record
from tests.sim.caller_scripts import RESPONSIVE_SMS, SPANISH_SMS


@pytest.mark.llm
@pytest.mark.integration
@pytest.mark.asyncio
async def test_responsive_sms_intake_path(require_llm, db_session):
    record = await create_lead(
        lead_id="async-responsive-001",
        identity={"name": "David", "phone": ["+14805551234"], "email": ["david@example.com"]},
        consent={"origin": "inbound", "channels_allowed": ["sms", "email", "voice"], "ai_voice_consent": True},
    )

    for msg in RESPONSIVE_SMS:
        await process_inbound(
            "async-responsive-001",
            {"channel": "sms", "message": msg, "sender": "+14805551234", "event_type": "sms_inbound"},
        )

    updated = await load_lead_record("async-responsive-001")
    assert updated is not None
    assert updated.disposition in (Disposition.ENGAGED, Disposition.INTAKE_COMPLETE, Disposition.RETAINER_SENT)
    assert len(updated.intake_slots) >= 3


@pytest.mark.llm
@pytest.mark.integration
@pytest.mark.asyncio
async def test_spanish_sms_path(require_llm, db_session):
    await create_lead(
        lead_id="async-spanish-001",
        identity={"name": "Maria", "phone": ["+14805552001"], "email": ["maria@example.com"]},
        consent={"origin": "inbound", "channels_allowed": ["sms"], "ai_voice_consent": True},
    )

    for msg in SPANISH_SMS:
        await process_inbound(
            "async-spanish-001",
            {"channel": "sms", "message": msg, "sender": "+14805552001", "event_type": "sms_inbound"},
        )

    updated = await load_lead_record("async-spanish-001")
    assert updated is not None
    assert updated.engagement.language_observed == "es" or len(updated.intake_slots) >= 2


@pytest.mark.llm
@pytest.mark.integration
@pytest.mark.asyncio
async def test_retainer_signed_flow(require_llm, db_session):
    await create_lead(
        lead_id="async-retainer-001",
        identity={"name": "David", "phone": ["+14805551234"], "email": ["david@example.com"]},
        consent={"origin": "inbound", "channels_allowed": ["sms"], "ai_voice_consent": True},
    )

    await send_retainer("async-retainer-001", "en", "sms")
    result = await process_inbound(
        "async-retainer-001",
        {
            "channel": "esign",
            "event_type": "retainer_signed",
            "envelope_id": "mock-env-001",
            "signed_pdf_ref": "mock.pdf",
        },
    )

    assert result.get("disposition") == Disposition.SIGNED.value
    updated = await load_lead_record("async-retainer-001")
    assert updated is not None
    assert updated.disposition == Disposition.SIGNED
