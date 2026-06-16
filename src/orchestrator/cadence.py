"""Deterministic cadence policy — no LLM."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from src.models.enums import Channel, MessageGoal


@dataclass(frozen=True)
class CadenceStep:
    step: int
    channel: Channel | None
    touch_type: str
    backoff_seconds: int
    template_hint: str = ""


CADENCE: list[CadenceStep] = [
    CadenceStep(0, Channel.SMS, "instant_reply", 0, "warm_strike"),
    CadenceStep(1, Channel.VOICE, "call_attempt", 300, "first_call"),
    CadenceStep(2, Channel.SMS, "follow_up", 3600, "tried_reach"),
    CadenceStep(3, Channel.VOICE, "call_attempt_alt_hour", 86400, "second_call"),
    CadenceStep(4, Channel.EMAIL, "intake_link", 172800, "email_intake"),
    CadenceStep(5, Channel.VOICE, "call_attempt", 345600, "third_call"),
    CadenceStep(6, Channel.SMS, "loss_aversion", 604800, "close_file"),
    CadenceStep(7, Channel.EMAIL, "final", 864000, "final_email"),
    CadenceStep(8, None, "give_up", 1209600, "terminal"),
]


def get_cadence_step(attempt: int) -> CadenceStep:
    """Get cadence step for attempt number (1-indexed in workflow loop)."""
    idx = min(attempt, len(CADENCE) - 1)
    return CADENCE[idx]


def get_backoff_seconds(attempt: int) -> int:
    return get_cadence_step(attempt).backoff_seconds


def should_skip_voice_hour(last_voice_hour: int | None, tz: ZoneInfo = ZoneInfo("America/Phoenix")) -> bool:
    """Call #2 must be at a different hour than call #1."""
    if last_voice_hour is None:
        return False
    current_hour = datetime.now(tz).hour
    return current_hour == last_voice_hour


def loss_aversion_message(name: str | None = None) -> str:
    who = name or "there"
    return (
        f"Hi {who}, should I go ahead and close your file? "
        "Just reply YES to keep it open."
    )


def paired_sms_after_voicemail(attempt: int) -> str:
    return (
        "Hi — I just left you a voicemail from Marigold Injury Law. "
        "You can reply to this text whenever works for you."
        if attempt == 1
        else f"Hi — tried calling again (attempt {attempt}). Reply here anytime."
    )


def touch_type_to_message_goal(touch_type: str) -> MessageGoal:
    mapping = {
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
    return mapping.get(touch_type, MessageGoal.FOLLOW_UP)
