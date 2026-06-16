"""Projection and CQRS tests."""
from __future__ import annotations

import pytest

from src.models.enums import Channel, Disposition, EventType, IntakeField
from src.models.events import Event
from src.models.intake import IntakeRecord
from src.store.projection import apply_event


@pytest.fixture
def empty_record() -> IntakeRecord:
    return IntakeRecord(lead_id="proj-001", identity={"name": "Test"})


def test_slot_captured_event(empty_record: IntakeRecord):
    event = Event(
        event_type=EventType.SLOT_CAPTURED,
        lead_id="proj-001",
        channel=Channel.SMS,
        data={"field": "accident_date", "value": "2026-03-03", "confidence": 0.9},
    )
    updated = apply_event(empty_record, event)
    assert updated.intake_slots["accident_date"].value == "2026-03-03"


def test_outbound_increments_attempts(empty_record: IntakeRecord):
    event = Event(
        event_type=EventType.SMS_OUTBOUND,
        lead_id="proj-001",
        channel=Channel.SMS,
        data={"content": "hello"},
    )
    updated = apply_event(empty_record, event)
    assert updated.engagement.attempts_by_channel.get(Channel.SMS, 0) == 1
    assert updated.disposition == Disposition.CHASING


def test_opt_out_terminal(empty_record: IntakeRecord):
    event = Event(
        event_type=EventType.CONSENT_REVOKED,
        lead_id="proj-001",
        data={},
    )
    updated = apply_event(empty_record, event)
    assert updated.disposition == Disposition.OPTED_OUT


def test_intake_complete_when_all_slots(empty_record: IntakeRecord):
    record = empty_record
    for field in [
        IntakeField.ACCIDENT_DATE,
        IntakeField.ACCIDENT_LOCATION,
        IntakeField.ACCIDENT_TYPE,
        IntakeField.INJURIES,
        IntakeField.TREATMENT_STATUS,
        IntakeField.AT_FAULT_INSURANCE,
        IntakeField.PRIOR_ATTORNEY_CONTACT,
    ]:
        record.set_slot(field, "value", 0.9, "test")
    event = Event(
        event_type=EventType.SLOT_CAPTURED,
        lead_id="proj-001",
        channel=Channel.VOICE,
        data={"field": "accident_date", "value": "2026-03-03", "confidence": 0.9},
    )
    updated = apply_event(record, event)
    assert updated.required_slots_filled()
    assert updated.disposition == Disposition.INTAKE_COMPLETE
