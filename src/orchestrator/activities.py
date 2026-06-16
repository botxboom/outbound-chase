"""Temporal activities for the outbound chase workflow."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from temporalio import activity

from src.models.enums import Channel, Disposition, EventType, IntakeField, MessageGoal, Sentiment
from src.models.intake import IntakeRecord
from src.orchestrator.cadence import (
    get_backoff_seconds,
    loss_aversion_message,
    paired_sms_after_voicemail,
    should_skip_voice_hour,
)
from src.orchestrator.strategy import TouchPlan, plan_next_touch as compute_plan


def _settings():
    from src.config import settings

    return settings


def _store():
    from src.store import projection

    return projection


@activity.defn
async def load_lead(lead_id: str) -> dict:
    data = await _store().load_lead_with_context(lead_id)
    return data or {}


@activity.defn
async def save_lead(record: dict) -> None:
    intake = IntakeRecord.model_validate(record)
    await _store().persist_lead(intake)


@activity.defn
async def log_event(
    lead_id: str,
    event_type: str,
    channel: str | None,
    data: dict,
) -> None:
    await _store().append_event(lead_id, event_type, channel, data)


@activity.defn
async def check_compliance_gates(intake_record: dict, channel: str) -> dict:
    if intake_record.get("consent", {}).get("opt_out", False):
        return {
            "allowed": False,
            "terminal": True,
            "terminal_state": Disposition.OPTED_OUT.value,
            "reason": "opt_out",
        }

    lead_tz = ZoneInfo("America/Phoenix")
    now = datetime.now(lead_tz)
    if now.hour < _settings().quiet_hours_start or now.hour >= _settings().quiet_hours_end:
        return {
            "allowed": False,
            "terminal": False,
            "terminal_state": None,
            "reason": "quiet_hours",
        }

    if channel == Channel.VOICE.value:
        ai_consent = intake_record.get("consent", {}).get("ai_voice_consent", False)
        if not ai_consent and not _settings().demo_ai_voice_consent:
            return {
                "allowed": False,
                "terminal": False,
                "terminal_state": None,
                "reason": "no_ai_voice_consent",
            }

    engagement = intake_record.get("engagement", {})
    attempts = engagement.get("attempts_by_channel", {})
    channel_attempts = attempts.get(channel, 0)
    total_attempts = sum(attempts.values())

    caps = {
        Channel.VOICE.value: _settings().max_voice_attempts,
        Channel.SMS.value: _settings().max_sms_attempts,
        Channel.EMAIL.value: _settings().max_email_attempts,
    }
    if channel_attempts >= caps.get(channel, 5):
        return {
            "allowed": False,
            "terminal": False,
            "terminal_state": None,
            "reason": f"channel_cap_reached_{channel}",
        }

    if total_attempts >= _settings().max_total_attempts:
        return {
            "allowed": False,
            "terminal": True,
            "terminal_state": Disposition.GAVE_UP.value,
            "reason": "max_total_attempts",
        }

    created_at = intake_record.get("created_at")
    if created_at:
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        days_elapsed = (datetime.now(timezone.utc) - created_at).days
        if days_elapsed >= _settings().chase_timeout_days:
            return {
                "allowed": False,
                "terminal": True,
                "terminal_state": Disposition.GAVE_UP.value,
                "reason": "chase_timeout",
            }

    return {"allowed": True, "terminal": False, "terminal_state": None, "reason": None}


@activity.defn
async def extract_signals(lead_id: str, event_context: dict) -> dict:
    """Extract engagement signals from an interaction and persist."""
    from src.orchestrator.signal_extractor import extract_and_apply_signals

    record = await _store().load_lead_record(lead_id)
    if not record:
        return {}

    patch = await extract_and_apply_signals(record, event_context)
    patch_data = patch.to_dict()
    if patch_data:
        await _store().append_event(
            lead_id,
            EventType.STRATEGY_SIGNAL_OBSERVED.value,
            event_context.get("channel"),
            patch_data,
        )
        await _store().persist_lead(record)
    return patch_data


@activity.defn
async def plan_next_touch(
    lead_id: str,
    is_initial: bool = False,
    trigger: str | None = None,
) -> dict:
    """Plan the next outbound touch based on lead engagement model."""
    record = await _store().load_lead_record(lead_id)
    if not record:
        return TouchPlan(
            channel=Channel.SMS,
            message_goal=MessageGoal.GIVE_UP,
            wait_seconds=0,
            reason="no_record",
            terminal_action="give_up",
        ).to_dict()

    plan = compute_plan(record, is_initial=is_initial, trigger=trigger)

    if not plan.terminal_action:
        gate = await check_compliance_gates(record.model_dump(mode="json"), plan.channel.value)
        if gate.get("terminal"):
            plan = TouchPlan(
                channel=plan.channel,
                message_goal=MessageGoal.GIVE_UP,
                wait_seconds=0,
                reason=f"terminal:{gate.get('reason', 'gate')}",
                terminal_action="give_up",
            )
        elif not gate.get("allowed"):
            retry = compute_plan(
                record,
                exclude_channel=plan.channel,
                is_initial=is_initial,
                trigger=trigger,
            )
            plan = TouchPlan(
                channel=retry.channel,
                message_goal=retry.message_goal,
                wait_seconds=retry.wait_seconds,
                reason=f"{retry.reason}|gate_blocked_{plan.channel.value}",
                constraints=retry.constraints,
            )

    await _store().append_event(
        lead_id,
        EventType.STRATEGY_PLANNED.value,
        plan.channel.value,
        {
            **plan.to_dict(),
            "advance_cadence": not is_initial and plan.terminal_action is None,
        },
    )
    record = await _store().load_lead_record(lead_id)
    if record:
        await _store().persist_lead(record)

    return plan.to_dict()


@activity.defn
async def determine_channel(intake_record: dict, attempt: int) -> str:
    from src.orchestrator.cadence import get_cadence_step

    step = get_cadence_step(attempt)
    if step.channel is None:
        return Channel.SMS.value

    channel = step.channel.value
    consent = intake_record.get("consent", {})
    channels_allowed = consent.get("channels_allowed", [])
    ai_voice_consent = consent.get("ai_voice_consent", False) or _settings().demo_ai_voice_consent
    attempts = intake_record.get("engagement", {}).get("attempts_by_channel", {})

    if channel == Channel.VOICE.value:
        if Channel.VOICE.value not in channels_allowed or not ai_voice_consent:
            return Channel.SMS.value
        if attempts.get(Channel.VOICE.value, 0) >= _settings().max_voice_attempts:
            return Channel.SMS.value
        last_hour = intake_record.get("engagement", {}).get("best_time_hint")
        if step.touch_type == "call_attempt_alt_hour" and last_hour:
            if should_skip_voice_hour(int(last_hour)):
                return Channel.SMS.value

    if channel not in channels_allowed:
        if Channel.SMS.value in channels_allowed:
            return Channel.SMS.value
        return Channel.EMAIL.value

    return channel


@activity.defn
async def execute_brain_call(
    intake_record: dict,
    channel: str,
    lead_id: str,
    inbound_message: str | None = None,
    voice_context: str | None = None,
    message_goal: str | None = None,
    plan_reason: str | None = None,
    constraints: list[str] | None = None,
) -> dict:
    from src.brain.agent import ConversationBrain

    brain = ConversationBrain()
    if voice_context:
        intake_record = {
            **intake_record,
            "_voice_context": voice_context,
        }
    return await brain.run_turn(
        intake_record=intake_record,
        channel=channel,
        lead_id=lead_id,
        inbound_message=inbound_message,
        message_goal=message_goal,
        plan_reason=plan_reason,
        constraints=constraints,
    )


@activity.defn
async def send_via_channel(channel: str, content: dict, lead_id: str) -> dict:
    from src.channels import get_adapter

    record = await _store().load_lead_record(lead_id)
    if not record:
        return {"success": False, "error": "lead not found"}

    identity = record.identity
    enriched = dict(content)
    if channel == Channel.VOICE.value:
        phones = identity.get("phone", [])
        enriched["phone_number"] = phones[0] if phones else None
        enriched["amd_result"] = content.get("amd_result", "machine")
        voice_attempts = record.engagement.attempts_by_channel.get(Channel.VOICE, 0)
        enriched["voicemail_attempt"] = voice_attempts + 1
    elif channel == Channel.SMS.value:
        phones = identity.get("phone", [])
        enriched["phone_number"] = phones[0] if phones else None
    elif channel == Channel.EMAIL.value:
        emails = identity.get("email", [])
        enriched["email"] = emails[0] if emails else None

    adapter = get_adapter(channel)
    result = await adapter.send(lead_id=lead_id, content=enriched)
    return result


@activity.defn
async def write_to_clio(intake_record: dict, lead_id: str, milestone: str | None = None) -> dict:
    from src.integrations.clio import ClioClient

    client = ClioClient()
    return await client.write_lead(
        intake_record=intake_record,
        lead_id=lead_id,
        milestone=milestone,
    )


@activity.defn
async def schedule_callback(lead_id: str, callback_time: str) -> None:
    activity.logger.info("Callback scheduled for %s at %s", lead_id, callback_time)


@activity.defn
async def send_retainer(lead_id: str, language: str, channel: str) -> dict:
    from src.integrations.dbox_sign import send_retainer_for_signing

    record = await _store().load_lead_record(lead_id)
    identity = record.identity if record else {}

    result = await send_retainer_for_signing(
        lead_id=lead_id,
        language=language,
        channel=channel,
        signer_name=identity.get("name", ""),
        signer_email=(identity.get("email") or [""])[0] if identity.get("email") else "",
    )

    if result.get("success"):
        await _store().append_event(
            lead_id,
            EventType.RETAINER_SENT.value,
            channel,
            {"envelope_id": result.get("envelope_id"), "language": language},
        )

    return result


@activity.defn
async def process_inbound(lead_id: str, event_data: dict) -> dict:
    """Append inbound event, invoke brain, send reply."""
    channel = event_data.get("channel", "sms")
    message = event_data.get("message", "")

    event_type_map = {
        "sms": EventType.SMS_INBOUND.value,
        "email": EventType.EMAIL_INBOUND.value,
        "voice": EventType.CALL_ANSWERED.value,
        "esign": EventType.RETAINER_SIGNED.value,
    }

    if event_data.get("event_type") == "retainer_signed":
        await _store().append_event(
            lead_id,
            EventType.RETAINER_SIGNED.value,
            "esign",
            {
                "envelope_id": event_data.get("envelope_id"),
                "signed_pdf_ref": event_data.get("signed_pdf_ref", f"mock-pdf-{lead_id}"),
            },
        )
        record = await _store().load_lead_record(lead_id)
        return {
            "action": "retainer_signed",
            "disposition": Disposition.SIGNED.value,
            "record": record.model_dump(mode="json") if record else {},
        }

    await _store().append_event(
        lead_id,
        event_type_map.get(channel, EventType.SMS_INBOUND.value),
        channel,
        {"message": message, "sender": event_data.get("sender", "")},
    )

    record = await _store().load_lead_with_context(lead_id)
    if not record:
        return {"action": "error"}

    await extract_signals(
        lead_id,
        {
            "direction": "inbound",
            "channel": channel,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )

    record = await _store().load_lead_with_context(lead_id)
    if not record:
        return {"action": "error"}

    brain_result = await execute_brain_call(
        record,
        channel,
        lead_id,
        inbound_message=message,
    )
    await _apply_brain_result(
        lead_id, channel, brain_result, IntakeRecord.model_validate(record)
    )
    updated = await _store().load_lead_record(lead_id)
    brain_result["record"] = updated.model_dump(mode="json") if updated else record
    return brain_result


@activity.defn
async def handle_voice_outcome(
    lead_id: str,
    intake_record: dict,
    amd_result: str,
    message_goal: str = MessageGoal.CALL_ATTEMPT.value,
    plan_reason: str = "",
    constraints: list[str] | None = None,
) -> dict:
    """Branch on AMD: human converse, machine leave voicemail + paired SMS."""
    channel = Channel.VOICE.value
    record = IntakeRecord.model_validate(intake_record)
    attempts = record.engagement.attempts_by_channel
    voice_attempts = attempts.get(Channel.VOICE, attempts.get("voice", 0)) + 1

    await _store().append_event(
        lead_id,
        EventType.CALL_ATTEMPTED.value,
        channel,
        {"amd_result": amd_result, "attempt": voice_attempts, "hour": datetime.now(ZoneInfo("America/Phoenix")).hour},
    )

    record = await _store().load_lead_record(lead_id)
    assert record is not None

    if amd_result in ("machine", "unknown"):
        if _settings().use_mock_channels:
            vm_text = (
                f"Hi, this is Marigold Injury Law — attempt {voice_attempts} to reach you "
                "about your accident. Please call or text us back when you can."
            )
            brain_result = {"content": {"text": vm_text}}
        else:
            vm_context = (
                f"Leave a unique voicemail message (attempt #{voice_attempts}). "
                f"Message goal: {message_goal}. "
                "Reference prior attempts if any. Keep it warm and under 30 seconds. "
                "Do NOT repeat prior voicemails."
            )
            record_ctx = await _store().load_lead_with_context(lead_id)
            brain_result = await execute_brain_call(
                record_ctx or record.model_dump(mode="json"),
                channel,
                lead_id,
                voice_context=vm_context,
                message_goal=message_goal,
                plan_reason=plan_reason,
                constraints=constraints,
            )
            vm_text = (brain_result.get("content") or {}).get("text", "Hi, following up from Marigold Injury Law.")
        await _store().append_event(
            lead_id,
            EventType.CALL_VOICEMAIL_LEFT.value,
            channel,
            {"content": vm_text, "attempt": voice_attempts},
        )

        sms_text = paired_sms_after_voicemail(voice_attempts)
        await send_via_channel(
            Channel.SMS.value,
            {"text": sms_text},
            lead_id,
        )
        await _store().append_event(
            lead_id,
            EventType.SMS_OUTBOUND.value,
            Channel.SMS.value,
            {"content": sms_text, "paired_with_voicemail": True},
        )
        record = await _store().load_lead_record(lead_id)
        return {
            "amd_result": amd_result,
            "voicemail": vm_text,
            "paired_sms": sms_text,
            "record": record.model_dump(mode="json") if record else {},
        }

    # Human answered
    if _settings().use_mock_channels and not intake_record.get("_force_brain"):
        brain_result = {
            "content": {
                "text": "Hi, this is the automated assistant with Marigold Injury Law — do you have a moment?",
            }
        }
    else:
        record_ctx = await _store().load_lead_with_context(lead_id)
        brain_result = await execute_brain_call(
            record_ctx or record.model_dump(mode="json"),
            channel,
            lead_id,
            voice_context="Outbound call connected — human answered. Use standard opener.",
            message_goal=message_goal,
            plan_reason=plan_reason,
            constraints=constraints,
        )
    await _apply_brain_result(lead_id, channel, brain_result, record)
    record = await _store().load_lead_record(lead_id)
    return {
        "amd_result": "human",
        "brain_result": brain_result,
        "record": record.model_dump(mode="json") if record else {},
    }


@activity.defn
async def execute_outbound_touch(
    lead_id: str,
    intake_record: dict,
    channel: str,
    message_goal: str,
    attempt: int = 0,
    plan_reason: str = "",
    constraints: list[str] | None = None,
) -> dict:
    """Execute a single outbound touch driven by strategy plan."""
    record = IntakeRecord.model_validate(intake_record)

    if message_goal == MessageGoal.GIVE_UP.value:
        await _store().append_event(lead_id, EventType.GAVE_UP.value, None, {"reason": "no_response_14d"})
        return {"action": "stop", "disposition": Disposition.GAVE_UP.value}

    if message_goal == MessageGoal.SEND_RETAINER.value:
        lang = record.engagement.language_observed
        await send_retainer(lead_id, lang, channel)
        record = await _store().load_lead_record(lead_id)
        return {"action": "send_retainer", "record": record.model_dump(mode="json") if record else {}}

    if channel == Channel.VOICE.value:
        amd = "machine"
        from src.channels.mock import MockVoiceAdapter

        if lead_id in MockVoiceAdapter.amd_results:
            amd = MockVoiceAdapter.amd_results[lead_id]
        result = await handle_voice_outcome(
            lead_id,
            record.model_dump(mode="json"),
            amd,
            message_goal=message_goal,
            plan_reason=plan_reason,
            constraints=constraints,
        )
        await extract_signals(
            lead_id,
            {"direction": "outbound", "channel": channel, "timestamp": datetime.now(timezone.utc).isoformat()},
        )
        return result

    if message_goal == MessageGoal.LOSS_AVERSION.value:
        text = loss_aversion_message(record.identity.get("name"))
        await send_via_channel(channel, {"text": text}, lead_id)
        event_type = EventType.SMS_OUTBOUND.value if channel == Channel.SMS.value else EventType.EMAIL_SENT.value
        await _store().append_event(lead_id, event_type, channel, {"content": text, "message_goal": message_goal})
        record = await _store().load_lead_record(lead_id)
        await extract_signals(
            lead_id,
            {"direction": "outbound", "channel": channel, "timestamp": datetime.now(timezone.utc).isoformat()},
        )
        return {"content": text, "record": record.model_dump(mode="json") if record else {}}

    brain_result = await execute_brain_call(
        record.model_dump(mode="json"),
        channel,
        lead_id,
        message_goal=message_goal,
        plan_reason=plan_reason,
        constraints=constraints,
    )

    if message_goal == MessageGoal.INSTANT_REPLY.value and not brain_result.get("content"):
        brain_result["content"] = {
            "text": "Hi! This is Marigold's automated assistant (not a lawyer). Thanks for reaching out — when's a good time to collect some details?",
            "type": "text",
        }

    if (
        _settings().use_mock_channels
        and not plan_reason
        and message_goal in (MessageGoal.FOLLOW_UP.value, MessageGoal.INTAKE_LINK.value, MessageGoal.FINAL.value)
        and not brain_result.get("content")
    ):
        templates = {
            MessageGoal.FOLLOW_UP.value: "Tried you earlier — happy to do this all by text if that's easier.",
            MessageGoal.INTAKE_LINK.value: "Here's a link to complete your intake online when you're ready.",
            MessageGoal.FINAL.value: "Final follow-up from Marigold Injury Law — reply if you'd like to keep your file open.",
        }
        brain_result["content"] = {
            "text": templates.get(message_goal, "Following up from Marigold Injury Law."),
            "type": "text",
        }

    await _apply_brain_result(lead_id, channel, brain_result, record)
    record = await _store().load_lead_record(lead_id)
    await extract_signals(
        lead_id,
        {"direction": "outbound", "channel": channel, "timestamp": datetime.now(timezone.utc).isoformat()},
    )
    return {"brain_result": brain_result, "record": record.model_dump(mode="json") if record else {}}


async def _apply_brain_result(
    lead_id: str,
    channel: str,
    brain_result: dict,
    record: IntakeRecord,
) -> None:
    """Apply brain output: slots, disposition, send, events."""
    for slot_data in brain_result.get("slots", []):
        field_name = slot_data.get("field")
        if field_name == "preferred_lang":
            record.engagement.language_observed = slot_data.get("value", "en")
            continue
        try:
            field = IntakeField(field_name)
            record.set_slot(
                field=field,
                value=slot_data.get("value"),
                confidence=slot_data.get("confidence", 0.8),
                source_event=f"brain:{channel}",
            )
            await _store().append_event(
                lead_id,
                EventType.SLOT_CAPTURED.value,
                channel,
                {
                    "field": field_name,
                    "value": slot_data.get("value"),
                    "confidence": slot_data.get("confidence", 0.8),
                },
            )
        except (ValueError, TypeError):
            pass

    if brain_result.get("sentiment"):
        try:
            record.engagement.sentiment = Sentiment(brain_result["sentiment"])
        except ValueError:
            pass

    action = brain_result.get("action")
    if action == "stop":
        record.disposition = Disposition.OPTED_OUT
        await _store().append_event(lead_id, EventType.CONSENT_REVOKED.value, channel, {})
    elif action == "escalate":
        record.disposition = Disposition.HANDOFF
        await _store().append_event(
            lead_id,
            EventType.ESCALATED.value,
            channel,
            {"reason": brain_result.get("reason", "")},
        )
    elif action == "wrong_number":
        record.disposition = Disposition.WRONG_NUMBER
        await _store().append_event(lead_id, EventType.WRONG_NUMBER_DETECTED.value, channel, {})
    elif action == "send_retainer":
        lang = record.engagement.language_observed
        await send_retainer(lead_id, lang, brain_result.get("channel", "sms"))

    if record.required_slots_filled() and record.disposition not in (
        Disposition.RETAINER_SENT,
        Disposition.SIGNED,
        Disposition.OPTED_OUT,
        Disposition.WRONG_NUMBER,
        Disposition.HANDOFF,
        Disposition.GAVE_UP,
    ):
        record.disposition = Disposition.INTAKE_COMPLETE

    if brain_result.get("content"):
        await send_via_channel(channel, brain_result["content"], lead_id)
        event_type = {
            Channel.VOICE.value: EventType.CALL_ATTEMPTED.value,
            Channel.SMS.value: EventType.SMS_OUTBOUND.value,
            Channel.EMAIL.value: EventType.EMAIL_SENT.value,
        }.get(channel, EventType.SMS_OUTBOUND.value)
        if channel != Channel.VOICE.value:
            await _store().append_event(
                lead_id,
                event_type,
                channel,
                {"content": brain_result["content"].get("text", ""), "touch_type": "brain"},
            )

    await _store().persist_lead(record)


@activity.defn
async def get_backoff(attempt: int) -> int:
    return get_backoff_seconds(attempt)
