"""CQRS projection layer — event log is source of truth."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from src.db.models import EventLog, LeadRecord, async_session
from src.models.enums import Channel, Disposition, EngagementMode, EventType, IntakeField, Sentiment
from src.models.events import Event
from src.models.intake import ConsentRecord, EngagementState, IntakeRecord, IntakeSlot, RetainerState


def _default_consent() -> ConsentRecord:
    return ConsentRecord(
        origin="inbound",
        channels_allowed=[Channel.VOICE, Channel.SMS, Channel.EMAIL],
    )


def record_from_lead_row(row: LeadRecord) -> IntakeRecord:
    """Build IntakeRecord from LeadRecord DB row."""
    intake_data = row.intake_data or {}
    return IntakeRecord(
        lead_id=row.id,
        identity=intake_data.get("identity", {}),
        consent=ConsentRecord.model_validate(row.consent_data or _default_consent().model_dump()),
        intake_slots={
            k: IntakeSlot.model_validate(v) if isinstance(v, dict) else v
            for k, v in intake_data.get("intake_slots", {}).items()
        },
        engagement=EngagementState.model_validate(row.engagement_data or {}),
        disposition=Disposition(row.disposition),
        retainer=RetainerState.model_validate(row.retainer_data or {}),
        created_at=row.created_at or datetime.now(timezone.utc),
        updated_at=row.updated_at or datetime.now(timezone.utc),
    )


def record_to_lead_payload(record: IntakeRecord) -> dict[str, Any]:
    """Serialize IntakeRecord into LeadRecord column dicts."""
    return {
        "intake_data": {
            "identity": record.identity,
            "intake_slots": {k: v.model_dump(mode="json") for k, v in record.intake_slots.items()},
        },
        "consent_data": record.consent.model_dump(mode="json"),
        "engagement_data": record.engagement.model_dump(mode="json"),
        "retainer_data": record.retainer.model_dump(mode="json"),
        "disposition": record.disposition.value,
        "language": record.engagement.language_observed,
    }


async def get_conversation_history(lead_id: str, limit: int = 20) -> list[dict[str, str]]:
    """Build conversation turns from recent inbound/outbound events."""
    async with async_session() as session:
        result = await session.execute(
            select(EventLog)
            .where(EventLog.lead_id == lead_id)
            .order_by(EventLog.sequence.desc())
            .limit(limit)
        )
        rows = list(reversed(result.scalars().all()))

    turns: list[dict[str, str]] = []
    inbound_types = {"sms.inbound", "email.inbound", "call.answered"}
    outbound_types = {"sms.outbound", "email.sent", "call.voicemail_left"}

    for row in rows:
        if row.event_type in inbound_types:
            msg = (row.data or {}).get("message", "")
            if msg:
                turns.append({"role": "user", "content": msg})
        elif row.event_type in outbound_types:
            msg = (row.data or {}).get("content", "")
            if msg:
                turns.append({"role": "assistant", "content": msg})
    return turns


async def load_lead_record(lead_id: str) -> IntakeRecord | None:
    async with async_session() as session:
        result = await session.execute(select(LeadRecord).where(LeadRecord.id == lead_id))
        row = result.scalar_one_or_none()
        if not row:
            return None
        return record_from_lead_row(row)


async def load_lead_with_context(lead_id: str) -> dict | None:
    """Load lead record dict with conversation history attached."""
    record = await load_lead_record(lead_id)
    if not record:
        return None
    data = record.model_dump(mode="json")
    data["_conversation"] = await get_conversation_history(lead_id)
    return data


async def persist_lead(record: IntakeRecord) -> None:
    """Write projection columns on LeadRecord."""
    payload = record_to_lead_payload(record)
    async with async_session() as session:
        result = await session.execute(select(LeadRecord).where(LeadRecord.id == record.lead_id))
        row = result.scalar_one_or_none()
        if not row:
            row = LeadRecord(id=record.lead_id)
            session.add(row)
        row.intake_data = payload["intake_data"]
        row.consent_data = payload["consent_data"]
        row.engagement_data = payload["engagement_data"]
        row.retainer_data = payload["retainer_data"]
        row.disposition = payload["disposition"]
        row.language = payload["language"]
        row.updated_at = datetime.now(timezone.utc)
        await session.commit()


async def _next_sequence(session: Any, lead_id: str) -> int:
    result = await session.execute(
        select(func.coalesce(func.max(EventLog.sequence), 0)).where(EventLog.lead_id == lead_id)
    )
    current = result.scalar_one()
    return int(current) + 1


async def append_event(
    lead_id: str,
    event_type: str,
    channel: str | None,
    data: dict[str, Any],
) -> Event:
    """Append event to log and apply to projection."""
    event_id = uuid4().hex
    now = datetime.now(timezone.utc)

    async with async_session() as session:
        sequence = await _next_sequence(session, lead_id)
        try:
            et = EventType(event_type)
        except ValueError:
            et = EventType.LEAD_CREATED  # fallback for custom types like inbound.engaged

        event = Event(
            event_id=event_id,
            event_type=et,
            lead_id=lead_id,
            channel=Channel(channel) if channel else None,
            timestamp=now,
            data=data,
            sequence=sequence,
        )

        log_row = EventLog(
            event_id=event_id,
            lead_id=lead_id,
            event_type=event_type,
            channel=channel,
            sequence=sequence,
            data=data,
            timestamp=now,
        )
        session.add(log_row)
        await session.commit()

    record = await load_lead_record(lead_id)
    if record:
        record = apply_event(record, event)
        await persist_lead(record)

    return event


async def rebuild_projection(lead_id: str) -> IntakeRecord | None:
    """Rebuild IntakeRecord by folding all events in sequence order."""
    async with async_session() as session:
        result = await session.execute(
            select(LeadRecord).where(LeadRecord.id == lead_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None

        events_result = await session.execute(
            select(EventLog)
            .where(EventLog.lead_id == lead_id)
            .order_by(EventLog.sequence)
        )
        event_rows = events_result.scalars().all()

    record = IntakeRecord(
        lead_id=lead_id,
        identity=(row.intake_data or {}).get("identity", {}),
        consent=_default_consent(),
        created_at=row.created_at or datetime.now(timezone.utc),
    )

    for er in event_rows:
        try:
            et = EventType(er.event_type)
        except ValueError:
            continue
        event = Event(
            event_id=er.event_id,
            event_type=et,
            lead_id=lead_id,
            channel=Channel(er.channel) if er.channel else None,
            timestamp=er.timestamp,
            data=er.data or {},
            sequence=er.sequence,
        )
        record = apply_event(record, event)

    await persist_lead(record)
    return record


def apply_event(record: IntakeRecord, event: Event) -> IntakeRecord:
    """Pure reducer — apply a single event to projection."""
    et = event.event_type.value
    data = event.data
    channel = event.channel.value if event.channel else None

    record.updated_at = datetime.now(timezone.utc)

    if et == EventType.LEAD_CREATED.value:
        if data.get("identity"):
            record.identity = data["identity"]
        if data.get("consent"):
            record.consent = ConsentRecord.model_validate(data["consent"])
        if data.get("trigger"):
            record.engagement.trigger = data["trigger"]
        record.disposition = Disposition.NEW

    elif et == EventType.FORM_SUBMITTED.value:
        for field_name, slot_data in data.get("slots", {}).items():
            try:
                field = IntakeField(field_name)
            except ValueError:
                continue
            record.set_slot(
                field=field,
                value=slot_data.get("value"),
                confidence=slot_data.get("confidence", 0.6),
                source_event=et,
            )

    elif et == EventType.SLOT_CAPTURED.value:
        field_name = data.get("field")
        if field_name:
            try:
                field = IntakeField(field_name)
                record.set_slot(
                    field=field,
                    value=data.get("value"),
                    confidence=data.get("confidence", 0.8),
                    source_event=data.get("source_event", et),
                )
            except ValueError:
                pass

    elif et == EventType.SLOT_CONFIRMED.value:
        field_name = data.get("field")
        if field_name:
            try:
                record.confirm_slot(IntakeField(field_name))
            except ValueError:
                pass

    elif et == EventType.CONSENT_CAPTURED.value:
        if data.get("origin"):
            record.consent.origin = data["origin"]
        if data.get("channels_allowed"):
            record.consent.channels_allowed = [Channel(c) for c in data["channels_allowed"]]
        if "ai_voice_consent" in data:
            record.consent.ai_voice_consent = data["ai_voice_consent"]
        if "recording_consent" in data:
            record.consent.recording_consent = data["recording_consent"]
        record.consent.captured_at = event.timestamp
        record.consent.source_event = et

    elif et == EventType.CONSENT_REVOKED.value:
        record.consent.opt_out = True
        record.disposition = Disposition.OPTED_OUT

    elif et in (
        EventType.CALL_ATTEMPTED.value,
        EventType.SMS_OUTBOUND.value,
        EventType.EMAIL_SENT.value,
    ):
        ch = channel or data.get("channel", Channel.SMS.value)
        try:
            ch_enum = Channel(ch)
        except ValueError:
            ch_enum = Channel.SMS
        current = record.engagement.attempts_by_channel.get(ch_enum, 0)
        record.engagement.attempts_by_channel[ch_enum] = current + 1
        record.engagement.last_touch = event.timestamp
        if record.disposition == Disposition.NEW:
            record.disposition = Disposition.CHASING
        if ch == Channel.VOICE.value and data.get("hour") is not None:
            record.engagement.best_time_hint = str(data["hour"])

    elif et in (EventType.SMS_INBOUND.value, EventType.EMAIL_INBOUND.value, EventType.CALL_ANSWERED.value):
        record.disposition = Disposition.ENGAGED
        if data.get("message"):
            lang = _detect_language(data["message"])
            if lang:
                record.engagement.language_observed = lang

    elif et == EventType.SENTIMENT_CHANGED.value:
        try:
            record.engagement.sentiment = Sentiment(data.get("sentiment", "neutral"))
        except ValueError:
            pass

    elif et == EventType.STRATEGY_SIGNAL_OBSERVED.value:
        if data.get("responsiveness"):
            record.engagement.responsiveness.update(data["responsiveness"])
        if data.get("channel_preference"):
            try:
                record.engagement.channel_preference = Channel(data["channel_preference"])
            except ValueError:
                pass
        if "do_not_call" in data:
            record.engagement.do_not_call = bool(data["do_not_call"])
        if data.get("contact_window"):
            record.engagement.contact_window = data["contact_window"]
        if data.get("engagement_mode"):
            try:
                record.engagement.engagement_mode = EngagementMode(data["engagement_mode"])
            except ValueError:
                pass
        if data.get("sentiment"):
            try:
                record.engagement.sentiment = Sentiment(data["sentiment"])
            except ValueError:
                pass
        for obj in data.get("objections") or []:
            if obj not in record.engagement.objections:
                record.engagement.objections.append(obj)
        if data.get("last_inbound_at"):
            try:
                record.engagement.last_inbound_at = datetime.fromisoformat(
                    str(data["last_inbound_at"]).replace("Z", "+00:00")
                )
            except ValueError:
                pass
        if data.get("last_inbound_latency_sec") is not None:
            record.engagement.last_inbound_latency_sec = int(data["last_inbound_latency_sec"])

    elif et == EventType.STRATEGY_PLANNED.value:
        record.engagement.last_plan = {
            "channel": data.get("channel"),
            "message_goal": data.get("message_goal"),
            "reason": data.get("reason"),
            "wait_seconds": data.get("wait_seconds"),
        }
        if data.get("advance_cadence"):
            record.engagement.cadence_step_index = min(
                record.engagement.cadence_step_index + 1,
                8,
            )

    elif et == EventType.RETAINER_SENT.value:
        record.retainer.envelope_id = data.get("envelope_id")
        record.retainer.status = "pending"
        record.disposition = Disposition.RETAINER_SENT

    elif et == EventType.RETAINER_SIGNED.value:
        record.retainer.status = "signed"
        record.retainer.signed_pdf_ref = data.get("signed_pdf_ref")
        record.disposition = Disposition.SIGNED

    elif et == EventType.ESCALATED.value:
        record.disposition = Disposition.HANDOFF

    elif et == EventType.GAVE_UP.value:
        record.disposition = Disposition.GAVE_UP

    elif et == EventType.WRONG_NUMBER_DETECTED.value:
        record.disposition = Disposition.WRONG_NUMBER

    elif et == "inbound.engaged":
        record.disposition = Disposition.ENGAGED

    elif et == "terminal.OPTED_OUT":
        record.disposition = Disposition.OPTED_OUT
    elif et == "terminal.GAVE_UP":
        record.disposition = Disposition.GAVE_UP
    elif et == "terminal.HANDOFF":
        record.disposition = Disposition.HANDOFF
    elif et == "terminal.WRONG_NUMBER":
        record.disposition = Disposition.WRONG_NUMBER
    elif et == "terminal.SIGNED":
        record.disposition = Disposition.SIGNED

    # Check intake complete after slot updates
    if record.required_slots_filled() and record.disposition in (
        Disposition.ENGAGED,
        Disposition.CHASING,
        Disposition.NEW,
    ):
        record.disposition = Disposition.INTAKE_COMPLETE

    return record


def _detect_language(text: str) -> str | None:
    """Simple Spanish detection."""
    spanish_markers = ["hola", "accidente", "gracias", "sí", "abogado", "llamaron"]
    lower = text.lower()
    if any(m in lower for m in spanish_markers):
        return "es"
    return None


async def create_lead(
    lead_id: str,
    identity: dict[str, Any],
    consent: dict[str, Any] | None = None,
    intake_slots: dict[str, Any] | None = None,
    trigger: str = "web_form",
) -> IntakeRecord:
    """Create a new lead with initial event."""
    consent_record = ConsentRecord.model_validate(consent or _default_consent().model_dump())
    record = IntakeRecord(
        lead_id=lead_id,
        identity=identity,
        consent=consent_record,
    )
    record.engagement.trigger = trigger

    if intake_slots:
        for field_name, slot_data in intake_slots.items():
            try:
                field = IntakeField(field_name)
                if isinstance(slot_data, dict):
                    record.set_slot(
                        field=field,
                        value=slot_data.get("value"),
                        confidence=slot_data.get("confidence", 0.6),
                        source_event=slot_data.get("source_event", trigger),
                    )
            except ValueError:
                pass

    async with async_session() as session:
        row = LeadRecord(
            id=lead_id,
            disposition=record.disposition.value,
            intake_data=record_to_lead_payload(record)["intake_data"],
            consent_data=record.consent.model_dump(mode="json"),
            engagement_data=record.engagement.model_dump(mode="json"),
            retainer_data={},
            language=record.engagement.language_observed,
        )
        session.add(row)
        await session.commit()

    await append_event(
        lead_id,
        EventType.LEAD_CREATED.value,
        None,
        {
            "identity": identity,
            "consent": consent_record.model_dump(mode="json"),
            "trigger": trigger,
        },
    )

    if intake_slots:
        await append_event(
            lead_id,
            EventType.FORM_SUBMITTED.value,
            None,
            {"slots": intake_slots},
        )

    result = await load_lead_record(lead_id)
    assert result is not None
    return result
