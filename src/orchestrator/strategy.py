"""Adaptive strategy — signal extraction and touch planning."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.config import settings
from src.models.enums import (
    Channel,
    Disposition,
    EngagementMode,
    MessageGoal,
    Responsiveness,
    Sentiment,
)
from src.models.intake import IntakeRecord
from src.orchestrator.cadence import get_cadence_step

HOSTILE_SIGNALS = [
    "stop calling",
    "how did you get",
    "don't call",
    "do not call",
    "hell",
    "damn",
    "leave me alone",
]
TEXT_PREFERENCE_SIGNALS = ["text me", "send a text", "sms only", "don't call", "do not call"]
TIME_WINDOW_PATTERNS = [
    (re.compile(r"\bafter\s+(\d+)\b", re.I), "after_{hour}"),
    (re.compile(r"\bevening\b", re.I), "evening"),
    (re.compile(r"\bmorning\b", re.I), "morning"),
    (re.compile(r"\bafternoon\b", re.I), "afternoon"),
]

TOUCH_TYPE_TO_GOAL: dict[str, MessageGoal] = {
    "instant_reply": MessageGoal.INSTANT_REPLY,
    "warm_intro": MessageGoal.WARM_INTRO,
    "confirm_slots": MessageGoal.CONFIRM_SLOTS,
    "gap_fill": MessageGoal.GAP_FILL,
    "soft_reengage": MessageGoal.SOFT_REENGAGE,
    "follow_up": MessageGoal.FOLLOW_UP,
    "call_attempt": MessageGoal.CALL_ATTEMPT,
    "call_attempt_alt_hour": MessageGoal.CALL_ATTEMPT,
    "intake_link": MessageGoal.INTAKE_LINK,
    "loss_aversion": MessageGoal.LOSS_AVERSION,
    "final": MessageGoal.FINAL,
    "give_up": MessageGoal.GIVE_UP,
}


@dataclass
class SignalPatch:
    responsiveness: dict[str, str] = field(default_factory=dict)
    channel_preference: Channel | None = None
    do_not_call: bool | None = None
    contact_window: str | None = None
    engagement_mode: EngagementMode | None = None
    sentiment: Sentiment | None = None
    objections: list[str] = field(default_factory=list)
    last_inbound_at: datetime | None = None
    last_inbound_latency_sec: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.responsiveness:
            data["responsiveness"] = self.responsiveness
        if self.channel_preference is not None:
            data["channel_preference"] = self.channel_preference.value
        if self.do_not_call is not None:
            data["do_not_call"] = self.do_not_call
        if self.contact_window is not None:
            data["contact_window"] = self.contact_window
        if self.engagement_mode is not None:
            data["engagement_mode"] = self.engagement_mode.value
        if self.sentiment is not None:
            data["sentiment"] = self.sentiment.value
        if self.objections:
            data["objections"] = self.objections
        if self.last_inbound_at is not None:
            data["last_inbound_at"] = self.last_inbound_at.isoformat()
        if self.last_inbound_latency_sec is not None:
            data["last_inbound_latency_sec"] = self.last_inbound_latency_sec
        return data


@dataclass
class TouchPlan:
    channel: Channel
    message_goal: MessageGoal
    wait_seconds: int
    reason: str
    terminal_action: str | None = None
    constraints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel.value,
            "message_goal": self.message_goal.value,
            "wait_seconds": self.wait_seconds,
            "reason": self.reason,
            "terminal_action": self.terminal_action,
            "constraints": self.constraints,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TouchPlan:
        channel_val = data.get("channel", Channel.SMS.value)
        goal_val = data.get("message_goal", MessageGoal.FOLLOW_UP.value)
        return cls(
            channel=Channel(channel_val),
            message_goal=MessageGoal(goal_val),
            wait_seconds=int(data.get("wait_seconds", 3600)),
            reason=data.get("reason", ""),
            terminal_action=data.get("terminal_action"),
            constraints=list(data.get("constraints") or []),
        )


def _parse_timestamp(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _total_attempts(record: IntakeRecord) -> int:
    return record.total_attempts()


def _sms_is_hot(record: IntakeRecord) -> bool:
    resp = record.engagement.responsiveness
    return resp.get(Channel.SMS.value) == Responsiveness.HOT.value


def _channel_allowed(record: IntakeRecord, channel: Channel) -> bool:
    allowed = record.consent.channels_allowed
    values = {c.value if isinstance(c, Channel) else str(c) for c in allowed}
    return channel.value in values


def _preferred_channel(record: IntakeRecord, exclude: Channel | None = None) -> Channel:
    pref = record.engagement.channel_preference
    if pref and pref != exclude:
        return pref
    if _sms_is_hot(record) and exclude != Channel.SMS:
        return Channel.SMS
    if _channel_allowed(record, Channel.SMS) and exclude != Channel.SMS:
        return Channel.SMS
    if _channel_allowed(record, Channel.EMAIL) and exclude != Channel.EMAIL:
        return Channel.EMAIL
    return Channel.SMS


def _voice_allowed(record: IntakeRecord) -> bool:
    if record.engagement.do_not_call:
        return False
    if record.engagement.channel_preference == Channel.SMS and _sms_is_hot(record):
        return False
    if not _channel_allowed(record, Channel.VOICE):
        return False
    if not record.consent.ai_voice_consent and not settings.demo_ai_voice_consent:
        return False
    voice_attempts = record.get_channel_attempts(Channel.VOICE)
    if voice_attempts >= settings.max_voice_attempts:
        return False
    return True


def _days_elapsed(record: IntakeRecord) -> int:
    created = record.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created).days


def _touch_type_to_goal(touch_type: str) -> MessageGoal:
    return TOUCH_TYPE_TO_GOAL.get(touch_type, MessageGoal.FOLLOW_UP)


def cadence_to_plan(record: IntakeRecord, step_index: int | None = None) -> TouchPlan:
    """Map cadence table step to TouchPlan (fallback when no strong signals)."""
    idx = step_index if step_index is not None else record.engagement.cadence_step_index
    step = get_cadence_step(idx)

    if step.touch_type == "give_up" or step.channel is None:
        return TouchPlan(
            channel=Channel.SMS,
            message_goal=MessageGoal.GIVE_UP,
            wait_seconds=0,
            reason="cadence_fallback:14d_timeout",
            terminal_action="give_up",
        )

    channel = step.channel
    if channel == Channel.VOICE and not _voice_allowed(record):
        channel = _preferred_channel(record, exclude=Channel.VOICE)

    return TouchPlan(
        channel=channel,
        message_goal=_touch_type_to_goal(step.touch_type),
        wait_seconds=step.backoff_seconds,
        reason=f"cadence_fallback:step_{idx}_{step.touch_type}",
    )


def apply_signal_patch(record: IntakeRecord, patch: SignalPatch) -> None:
    """Apply signal patch to engagement state in-memory."""
    eng = record.engagement
    for ch, level in patch.responsiveness.items():
        eng.responsiveness[ch] = level
    if patch.channel_preference is not None:
        eng.channel_preference = patch.channel_preference
    if patch.do_not_call is not None:
        eng.do_not_call = patch.do_not_call
    if patch.contact_window is not None:
        eng.contact_window = patch.contact_window
    if patch.engagement_mode is not None:
        eng.engagement_mode = patch.engagement_mode
    if patch.sentiment is not None:
        eng.sentiment = patch.sentiment
    for obj in patch.objections:
        if obj not in eng.objections:
            eng.objections.append(obj)
    if patch.last_inbound_at is not None:
        eng.last_inbound_at = patch.last_inbound_at
    if patch.last_inbound_latency_sec is not None:
        eng.last_inbound_latency_sec = patch.last_inbound_latency_sec


def extract_signals_rules(record: IntakeRecord, event_context: dict[str, Any]) -> SignalPatch:
    """Rule-based signal extraction from an interaction."""
    patch = SignalPatch()
    direction = event_context.get("direction", "inbound")
    channel = event_context.get("channel", Channel.SMS.value)
    message = (event_context.get("message") or "").lower()
    now = _parse_timestamp(event_context.get("timestamp")) or datetime.now(timezone.utc)

    if direction == "inbound":
        patch.last_inbound_at = now
        patch.responsiveness[channel] = Responsiveness.HOT.value
        patch.engagement_mode = EngagementMode.ENGAGED

        if channel in (Channel.SMS.value, Channel.WEB_CHAT.value):
            patch.channel_preference = Channel.SMS
            patch.responsiveness[Channel.SMS.value] = Responsiveness.HOT.value

        last_touch = record.engagement.last_touch
        if last_touch:
            if last_touch.tzinfo is None:
                last_touch = last_touch.replace(tzinfo=timezone.utc)
            patch.last_inbound_latency_sec = int((now - last_touch).total_seconds())

        if patch.last_inbound_latency_sec is not None and patch.last_inbound_latency_sec <= 300:
            patch.responsiveness[channel] = Responsiveness.HOT.value

        for signal in TEXT_PREFERENCE_SIGNALS:
            if signal in message:
                patch.do_not_call = True
                patch.channel_preference = Channel.SMS
                break

        for signal in HOSTILE_SIGNALS:
            if signal in message:
                patch.engagement_mode = EngagementMode.HOSTILE_OPEN
                patch.sentiment = Sentiment.HOSTILE
                if signal in ("stop calling", "don't call", "do not call", "leave me alone"):
                    patch.do_not_call = True
                    patch.channel_preference = Channel.SMS
                break

        if "already" in message and "lawyer" in message:
            patch.objections.append("has_lawyer")

        if "how did you get" in message or "who gave you" in message:
            patch.objections.append("privacy")

        for pattern, window in TIME_WINDOW_PATTERNS:
            match = pattern.search(message)
            if match:
                if "{hour}" in window and match.lastindex:
                    patch.contact_window = window.format(hour=match.group(1))
                else:
                    patch.contact_window = window
                break

    elif direction == "outbound":
        total = _total_attempts(record)
        has_inbound = record.engagement.last_inbound_at is not None
        if not has_inbound and total >= 3:
            patch.engagement_mode = EngagementMode.GHOSTING

    return patch


def plan_initial_touch(record: IntakeRecord, trigger: str | None = None) -> TouchPlan:
    """Ingress-aware first touch plan."""
    trigger = trigger or record.engagement.trigger or "web_form"
    origin = record.consent.origin
    has_slots = bool(record.intake_slots)

    if origin == "inbound":
        return TouchPlan(
            channel=Channel.SMS,
            message_goal=MessageGoal.INSTANT_REPLY,
            wait_seconds=0,
            reason="ingress:missed_call_instant_sms",
            constraints=["under 300 chars", "acknowledge missed call"],
        )

    if trigger == "clio_grow_stub":
        return TouchPlan(
            channel=Channel.SMS,
            message_goal=MessageGoal.SOFT_REENGAGE,
            wait_seconds=86400,
            reason="ingress:clio_grow_soft_reengage",
            constraints=["warm tone", "no pressure"],
        )

    if trigger == "web_chat":
        return TouchPlan(
            channel=Channel.SMS,
            message_goal=MessageGoal.GAP_FILL,
            wait_seconds=0,
            reason="ingress:web_chat_continue_on_sms",
            constraints=["reference prior chat if known", "under 300 chars"],
        )

    if trigger == "web_form" and has_slots:
        return TouchPlan(
            channel=Channel.SMS,
            message_goal=MessageGoal.CONFIRM_SLOTS,
            wait_seconds=0,
            reason="ingress:web_form_partial_confirm",
            constraints=["confirm known fields", "ask one missing gap"],
        )

    return TouchPlan(
        channel=Channel.SMS,
        message_goal=MessageGoal.WARM_INTRO,
        wait_seconds=0,
        reason="ingress:web_form_empty_warm_intro",
    )


def plan_next_touch(
    record: IntakeRecord,
    *,
    exclude_channel: Channel | None = None,
    is_initial: bool = False,
    trigger: str | None = None,
) -> TouchPlan:
    """Choose next touch based on person model; cadence as fallback."""
    if is_initial:
        return plan_initial_touch(record, trigger)

    # Terminal: chase timeout
    if _days_elapsed(record) >= settings.chase_timeout_days:
        return TouchPlan(
            channel=Channel.SMS,
            message_goal=MessageGoal.GIVE_UP,
            wait_seconds=0,
            reason="terminal:chase_timeout_14d",
            terminal_action="give_up",
        )

    total = _total_attempts(record)
    if total >= settings.max_total_attempts:
        return TouchPlan(
            channel=Channel.SMS,
            message_goal=MessageGoal.GIVE_UP,
            wait_seconds=0,
            reason="terminal:max_total_attempts",
            terminal_action="give_up",
        )

    if record.disposition == Disposition.INTAKE_COMPLETE:
        return TouchPlan(
            channel=_preferred_channel(record, exclude=exclude_channel),
            message_goal=MessageGoal.SEND_RETAINER,
            wait_seconds=0,
            reason="intake_complete:send_retainer",
        )

    # Hostile but not opted out — de-escalation SMS
    if (
        record.engagement.engagement_mode == EngagementMode.HOSTILE_OPEN
        and not record.consent.opt_out
        and record.disposition != Disposition.OPTED_OUT
    ):
        channel = Channel.SMS if exclude_channel != Channel.SMS else Channel.EMAIL
        return TouchPlan(
            channel=channel,
            message_goal=MessageGoal.EMPATHY_CHECK_IN,
            wait_seconds=172800,
            reason="hostile_open:deescalation_sms",
            constraints=["no voice mention", "offer opt-out", "under 300 chars"],
        )

    # SMS-hot or do_not_call — never voice
    if record.engagement.do_not_call or (_sms_is_hot(record) and record.engagement.channel_preference == Channel.SMS):
        channel = _preferred_channel(record, exclude=exclude_channel or Channel.VOICE)
        if channel == Channel.VOICE:
            channel = Channel.SMS
        goal = MessageGoal.GAP_FILL if record.intake_slots else MessageGoal.FOLLOW_UP
        if record.disposition == Disposition.ENGAGED:
            goal = MessageGoal.GAP_FILL
        return TouchPlan(
            channel=channel,
            message_goal=goal,
            wait_seconds=3600,
            reason="adaptive:sms_preferred_no_voice",
            constraints=["no voice mention", "under 300 chars"],
        )

    # Engaged with partial slots
    if record.disposition in (Disposition.ENGAGED, Disposition.CHASING) and record.intake_slots:
        if not record.required_slots_filled():
            channel = _preferred_channel(record, exclude=exclude_channel)
            if channel == Channel.VOICE and not _voice_allowed(record):
                channel = _preferred_channel(record, exclude=Channel.VOICE)
            return TouchPlan(
                channel=channel,
                message_goal=MessageGoal.GAP_FILL,
                wait_seconds=3600,
                reason="adaptive:engaged_gap_fill",
                constraints=["max 2 questions", "do not re-ask filled slots"],
            )

    # SMS hot — prefer SMS even without explicit preference
    if _sms_is_hot(record):
        channel = Channel.SMS if exclude_channel != Channel.SMS else Channel.EMAIL
        return TouchPlan(
            channel=channel,
            message_goal=MessageGoal.GAP_FILL if record.intake_slots else MessageGoal.FOLLOW_UP,
            wait_seconds=3600,
            reason="adaptive:sms_hot_responsive",
            constraints=["under 300 chars"],
        )

    # Fallback: cadence curve
    plan = cadence_to_plan(record)
    if exclude_channel and plan.channel == exclude_channel:
        alt = _preferred_channel(record, exclude=exclude_channel)
        plan = TouchPlan(
            channel=alt,
            message_goal=plan.message_goal,
            wait_seconds=plan.wait_seconds,
            reason=f"{plan.reason}|gate_retry_exclude_{exclude_channel.value}",
            constraints=plan.constraints,
        )
    if plan.channel == Channel.VOICE and not _voice_allowed(record):
        alt = _preferred_channel(record, exclude=Channel.VOICE)
        plan = TouchPlan(
            channel=alt,
            message_goal=MessageGoal.FOLLOW_UP,
            wait_seconds=plan.wait_seconds,
            reason=f"{plan.reason}|voice_not_allowed",
            constraints=plan.constraints,
        )
    return plan


def plan_with_gate_retry(
    record: IntakeRecord,
    gate_checker: Any,
    *,
    is_initial: bool = False,
    trigger: str | None = None,
) -> TouchPlan:
    """Plan next touch, re-planning if compliance gate blocks channel."""
    plan = plan_next_touch(record, is_initial=is_initial, trigger=trigger)
    if plan.terminal_action:
        return plan

    result = gate_checker(record.model_dump(mode="json"), plan.channel.value)
    if result.get("allowed"):
        return plan

    if result.get("terminal"):
        return TouchPlan(
            channel=plan.channel,
            message_goal=MessageGoal.GIVE_UP,
            wait_seconds=0,
            reason=f"terminal:{result.get('reason', 'gate')}",
            terminal_action="give_up",
        )

    retry = plan_next_touch(record, exclude_channel=plan.channel, is_initial=is_initial, trigger=trigger)
    retry.reason = f"{retry.reason}|gate_blocked_{plan.channel.value}"
    return retry
