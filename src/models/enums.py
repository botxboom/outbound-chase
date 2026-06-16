from __future__ import annotations

from enum import StrEnum


class Disposition(StrEnum):
    NEW = "NEW"
    CHASING = "CHASING"
    ENGAGED = "ENGAGED"
    INTAKE_COMPLETE = "INTAKE_COMPLETE"
    RETAINER_SENT = "RETAINER_SENT"
    SIGNED = "SIGNED"
    HANDOFF = "HANDOFF"
    GAVE_UP = "GAVE_UP"
    OPTED_OUT = "OPTED_OUT"
    WRONG_NUMBER = "WRONG_NUMBER"


class Channel(StrEnum):
    VOICE = "voice"
    SMS = "sms"
    EMAIL = "email"
    WEB_CHAT = "web_chat"


class EventType(StrEnum):
    # Lead lifecycle
    LEAD_CREATED = "lead.created"
    FORM_SUBMITTED = "form.submitted"

    # Call events
    CALL_ATTEMPTED = "call.attempted"
    CALL_NO_ANSWER = "call.no_answer"
    CALL_ANSWERED = "call.answered"
    CALL_VOICEMAIL_LEFT = "call.voicemail_left"
    CALL_MACHINE_DETECTED = "call.machine_detected"

    # SMS events
    SMS_OUTBOUND = "sms.outbound"
    SMS_INBOUND = "sms.inbound"

    # Email events
    EMAIL_SENT = "email.sent"
    EMAIL_OPENED = "email.opened"
    EMAIL_INBOUND = "email.inbound"

    # Intake events
    SLOT_CAPTURED = "slot.captured"
    SLOT_CONFIRMED = "slot.confirmed"

    # Consent events
    CONSENT_CAPTURED = "consent.captured"
    CONSENT_REVOKED = "consent.revoked"

    # Sentiment
    SENTIMENT_CHANGED = "sentiment.changed"

    # Strategy
    STRATEGY_SIGNAL_OBSERVED = "strategy.signal_observed"
    STRATEGY_PLANNED = "strategy.planned"

    # Retainer
    RETAINER_SENT = "retainer.sent"
    RETAINER_SIGNED = "retainer.signed"

    # Terminal events
    ESCALATED = "escalated"
    GAVE_UP = "gave_up"
    WRONG_NUMBER_DETECTED = "wrong_number.detected"


class Sentiment(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    HOSTILE = "hostile"
    DISTRESSED = "distressed"


class EngagementMode(StrEnum):
    ENGAGED = "engaged"
    SLOW_BURN = "slow_burn"
    GHOSTING = "ghosting"
    HOSTILE_OPEN = "hostile_open"


class Responsiveness(StrEnum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


class MessageGoal(StrEnum):
    INSTANT_REPLY = "instant_reply"
    WARM_INTRO = "warm_intro"
    CONFIRM_SLOTS = "confirm_slots"
    GAP_FILL = "gap_fill"
    EMPATHY_CHECK_IN = "empathy_check_in"
    SOFT_REENGAGE = "soft_reengage"
    FOLLOW_UP = "follow_up"
    CALL_ATTEMPT = "call_attempt"
    INTAKE_LINK = "intake_link"
    LOSS_AVERSION = "loss_aversion"
    FINAL = "final"
    SEND_RETAINER = "send_retainer"
    GIVE_UP = "give_up"


class IntakeField(StrEnum):
    NAME = "name"
    DOB = "dob"
    PHONE = "phone"
    EMAIL = "email"
    PREFERRED_LANG = "preferred_lang"
    ACCIDENT_DATE = "accident_date"
    ACCIDENT_LOCATION = "accident_location"
    ACCIDENT_TYPE = "accident_type"
    INJURIES = "injuries"
    TREATMENT_STATUS = "treatment_status"
    AT_FAULT_INSURANCE = "at_fault_insurance"
    PRIOR_ATTORNEY_CONTACT = "prior_attorney_contact"


# AZ statute of limitations
AZ_SOL_YEARS = 2
AZ_GOV_NOTICE_DAYS = 180
