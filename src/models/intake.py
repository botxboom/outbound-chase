from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from .enums import Channel, Disposition, EngagementMode, EventType, IntakeField, Sentiment


class IntakeSlot(BaseModel):
    field: IntakeField
    value: Any = None
    confidence: float = 0.0  # 0.0 to 1.0
    source_event: str | None = None
    captured_at: datetime | None = None
    confirmed: bool = False


class ConsentRecord(BaseModel):
    origin: str = "inbound"  # "inbound" | "outbound"
    channels_allowed: list[Channel] = Field(default_factory=lambda: [Channel.VOICE, Channel.SMS, Channel.EMAIL])
    ai_voice_consent: bool = False
    recording_consent: bool = False
    opt_out: bool = False
    captured_at: datetime | None = None
    source_event: str | None = None


def _default_consent() -> ConsentRecord:
    return ConsentRecord(
        origin="inbound",
        channels_allowed=[Channel.VOICE, Channel.SMS, Channel.EMAIL],
    )


class EngagementState(BaseModel):
    sentiment: Sentiment = Sentiment.NEUTRAL
    attempts_by_channel: dict[Channel, int] = Field(default_factory=dict)
    last_touch: datetime | None = None
    next_touch: datetime | None = None
    best_time_hint: str | None = None  # e.g. "morning", "evening"
    channel_preference: Channel | None = None
    language_observed: str = "en"
    responsiveness: dict[str, str] = Field(default_factory=dict)
    do_not_call: bool = False
    contact_window: str | None = None
    engagement_mode: EngagementMode = EngagementMode.SLOW_BURN
    objections: list[str] = Field(default_factory=list)
    last_inbound_at: datetime | None = None
    last_inbound_latency_sec: int | None = None
    last_plan: dict[str, Any] | None = None
    cadence_step_index: int = 0
    trigger: str = "web_form"


class RetainerState(BaseModel):
    envelope_id: str | None = None
    status: str | None = None  # "pending" | "viewed" | "signed"
    signed_pdf_ref: str | None = None


class IntakeRecord(BaseModel):
    lead_id: str
    identity: dict[str, Any] = Field(default_factory=dict)
    consent: ConsentRecord = Field(default_factory=_default_consent)
    intake_slots: dict[str, IntakeSlot] = Field(default_factory=dict)
    engagement: EngagementState = Field(default_factory=EngagementState)
    disposition: Disposition = Disposition.NEW
    retainer: RetainerState = Field(default_factory=RetainerState)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def get_slot(self, field: IntakeField) -> IntakeSlot | None:
        return self.intake_slots.get(field.value)

    def set_slot(
        self,
        field: IntakeField,
        value: Any,
        confidence: float,
        source_event: str,
    ) -> None:
        existing = self.intake_slots.get(field.value)
        if existing and existing.confirmed:
            return  # don't overwrite confirmed slots
        self.intake_slots[field.value] = IntakeSlot(
            field=field,
            value=value,
            confidence=confidence,
            source_event=source_event,
            captured_at=datetime.now(timezone.utc),
        )
        self.updated_at = datetime.now(timezone.utc)

    def confirm_slot(self, field: IntakeField) -> None:
        slot = self.intake_slots.get(field.value)
        if slot:
            slot.confirmed = True
            self.updated_at = datetime.now(timezone.utc)

    def required_slots_filled(self) -> bool:
        required = [
            IntakeField.ACCIDENT_DATE,
            IntakeField.ACCIDENT_LOCATION,
            IntakeField.ACCIDENT_TYPE,
            IntakeField.INJURIES,
            IntakeField.TREATMENT_STATUS,
            IntakeField.AT_FAULT_INSURANCE,
            IntakeField.PRIOR_ATTORNEY_CONTACT,
        ]
        return all(
            self.intake_slots.get(f.value) and self.intake_slots[f.value].value
            for f in required
        )

    def total_attempts(self) -> int:
        return sum(self.engagement.attempts_by_channel.values())

    def get_channel_attempts(self, channel: Channel) -> int:
        return self.engagement.attempts_by_channel.get(channel, 0)
