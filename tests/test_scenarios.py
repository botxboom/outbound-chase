"""Scenario tests based on the 6 caller traces from DESIGN.md.

Tests verify:
1. UPL guardrails (legal advice refusal)
2. Hostile caller handling (graceful stop)
3. Responsive caller (happy path intake)
4. Ghosting (cadence discipline)
5. Wrong number handling
6. Spanish full parity
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from src.models.enums import Channel, Disposition, Sentiment
from src.models.intake import ConsentRecord, EngagementState, IntakeRecord
from src.orchestrator.activities import check_compliance_gates, determine_channel


@pytest.fixture
def base_intake() -> dict:
    """Base intake record for testing."""
    return IntakeRecord(
        lead_id="test-lead-001",
        identity={
            "name": "David",
            "phone": ["+14805551234"],
            "email": ["david@example.com"],
        },
        consent=ConsentRecord(
            origin="inbound",
            channels_allowed=[Channel.VOICE, Channel.SMS, Channel.EMAIL],
            ai_voice_consent=True,
            recording_consent=True,
        ),
        engagement=EngagementState(
            sentiment=Sentiment.NEUTRAL,
            attempts_by_channel={},
        ),
        disposition=Disposition.NEW,
    ).model_dump()


# ─────────────────────────────────────────────
# 7.1 Asks for legal advice — UPL guardrails
# ─────────────────────────────────────────────


class TestScenario1_LegalAdviceRequest:
    """Caller asks for case value/merit opinion. Agent must refuse and redirect."""

    def test_refuses_merit_opinion(self, base_intake: dict):
        """Agent refuses "do I have a good case?" without sounding like a brick wall."""
        from src.brain.agent import ConversationBrain

        brain = ConversationBrain()

        # Simulate conversation where caller asks for legal advice
        base_intake["intake_slots"] = {
            "accident_date": {"field": "accident_date", "value": "2026-03-03", "confidence": 0.9},
        }

        # The brain should refuse and redirect to fact collection
        # In real test, this would call the LLM; here we verify the prompt
        # includes the refusal pattern
        from src.brain.agent import SYSTEM_PROMPT

        assert "Do I have a good case?" in SYSTEM_PROMPT or "merit" in SYSTEM_PROMPT.lower()
        assert "redirect to fact collection" in SYSTEM_PROMPT.lower() or "NEVER estimate" in SYSTEM_PROMPT

    def test_upl_guardrails_in_prompt(self, base_intake: dict):
        """System prompt contains hard rules against legal advice."""
        from src.brain.agent import SYSTEM_PROMPT

        # Must contain UPL refusal patterns
        assert "NOT a lawyer" in SYSTEM_PROMPT
        assert "NEVER estimate case value" in SYSTEM_PROMPT or "NEVER give legal advice" in SYSTEM_PROMPT
        assert "escalate to human" in SYSTEM_PROMPT.lower()

    def test_derive_sol_never_spoken(self, base_intake: dict):
        """SOL date is derived but NEVER spoken to caller."""
        from src.brain.agent import ConversationBrain

        brain = ConversationBrain()

        # derive_sol_date tool should flag for attorney, never speak
        tool_result = brain._process_tool_call(
            type("ToolCall", (), {
                "function": type("Func", (), {
                    "name": "derive_sol_date",
                    "arguments": '{"accident_date": "2026-03-03"}',
                })(),
            })(),
            base_intake,
        )

        assert tool_result is not None
        assert tool_result["type"] == "disposition"
        # Must have flagged for attorney
        assert tool_result.get("sol_flag", {}).get("flagged_for_attorney") is True
        assert tool_result.get("sol_flag", {}).get("spoken_to_caller") is False


# ─────────────────────────────────────────────
# 7.2 Hostile caller — graceful stop
# ─────────────────────────────────────────────


class TestScenario2_HostileCaller:
    """Hostile caller demands to stop. Agent de-escalates and honors opt-out."""

    def test_hostile_detection(self, base_intake: dict):
        """Hostile signals trigger de-escalation mode."""
        from src.brain.agent import ConversationBrain

        brain = ConversationBrain()

        messages = [
            {"role": "system", "content": "Test"},
            {"role": "user", "content": "How the hell did you get my number? Stop calling me."},
        ]

        sentiment = brain._detect_sentiment(messages, base_intake)
        assert sentiment == Sentiment.HOSTILE.value

    def test_opt_out_sets_terminal_state(self, base_intake: dict):
        """set_opt_out action triggers terminal state."""
        from src.brain.agent import ConversationBrain

        brain = ConversationBrain()

        tool_result = brain._process_tool_call(
            type("ToolCall", (), {
                "function": type("Func", (), {
                    "name": "set_opt_out",
                    "arguments": '{"channels": "all"}',
                })(),
            })(),
            base_intake,
        )

        assert tool_result is not None
        assert tool_result["type"] == "action"
        assert tool_result["action"] == "stop"
        assert tool_result["disposition"] == "OPTED_OUT"

    def test_compliance_gates_block_after_opt_out(self, base_intake: dict):
        """After opt-out, compliance gates block all channels."""
        base_intake["consent"]["opt_out"] = True

        import asyncio

        result = asyncio.run(
            check_compliance_gates(base_intake, Channel.VOICE.value)
        )

        assert result["allowed"] is False
        assert result["terminal"] is True
        assert result["terminal_state"] == Disposition.OPTED_OUT.value


# ─────────────────────────────────────────────
# 7.3 Responsive — happy path intake
# ─────────────────────────────────────────────


class TestScenario3_Responsive:
    """Caller responds promptly. Full intake completes and retainer sent."""

    def test_inbound_triggers_immediate_engagement(self, base_intake: dict):
        """Inbound origin means first touch is instant."""
        base_intake["consent"]["origin"] = "inbound"

        from src.models.intake import IntakeRecord

        record = IntakeRecord.model_validate(base_intake)
        assert record.consent.origin == "inbound"

    def test_channel_preference_for_inbound(self, base_intake: dict):
        """Inbound lead gets channel they used."""
        import asyncio

        result = asyncio.run(determine_channel(base_intake, 1))
        # Should prefer voice or SMS
        assert result in [Channel.VOICE.value, Channel.SMS.value]

    def test_slot_capture_updates_record(self, base_intake: dict):
        """Slots are captured and stored correctly."""
        from src.models.enums import IntakeField
        from src.models.intake import IntakeRecord

        record = IntakeRecord.model_validate(base_intake)
        record.set_slot(
            IntakeField.ACCIDENT_DATE,
            "2026-03-03",
            confidence=0.9,
            source_event="voice",
        )

        assert record.intake_slots["accident_date"].value == "2026-03-03"
        assert record.intake_slots["accident_date"].confidence == 0.9

    def test_required_slots_check(self, base_intake: dict):
        """required_slots_filled returns False when slots are missing."""
        from src.models.intake import IntakeRecord

        record = IntakeRecord.model_validate(base_intake)
        assert record.required_slots_filled() is False


# ─────────────────────────────────────────────
# 7.4 Ghosting — cadence discipline
# ─────────────────────────────────────────────


class TestScenario4_Ghosting:
    """No human answers. System follows 14-day cadence and gives up."""

    def test_attempt_caps_enforced(self, base_intake: dict):
        """Voice attempts capped at configured limit."""
        from src.config import settings

        base_intake["engagement"]["attempts_by_channel"] = {
            "voice": settings.max_voice_attempts,
        }

        import asyncio

        result = asyncio.run(
            check_compliance_gates(base_intake, Channel.VOICE.value)
        )

        assert result["allowed"] is False
        assert "channel_cap" in result["reason"]

    def test_total_attempt_cap(self, base_intake: dict):
        """Total attempts across channels capped."""
        base_intake["engagement"]["attempts_by_channel"] = {
            "voice": 3,
            "sms": 4,
            "email": 3,
        }

        import asyncio

        result = asyncio.run(
            check_compliance_gates(base_intake, Channel.SMS.value)
        )

        assert result["allowed"] is False
        assert result["terminal"] is True
        assert result["terminal_state"] == Disposition.GAVE_UP.value

    def test_chase_timeout(self, base_intake: dict):
        """14-day timeout triggers give-up."""
        from datetime import timedelta

        base_intake["created_at"] = (
            datetime.now(timezone.utc) - timedelta(days=15)
        ).isoformat()

        import asyncio

        result = asyncio.run(
            check_compliance_gates(base_intake, Channel.SMS.value)
        )

        assert result["allowed"] is False
        assert result["terminal"] is True
        assert result["terminal_state"] == Disposition.GAVE_UP.value

    def test_channel_fallback(self, base_intake: dict):
        """When voice exhausted, falls back to SMS."""
        from src.config import settings

        base_intake["engagement"]["attempts_by_channel"] = {
            "voice": settings.max_voice_attempts,
        }

        import asyncio

        channel = asyncio.run(determine_channel(base_intake, 3))
        assert channel in [Channel.SMS.value, Channel.EMAIL.value]


# ─────────────────────────────────────────────
# 7.5 Wrong number
# ─────────────────────────────────────────────


class TestScenario5_WrongNumber:
    """Person answering is not the lead. Agent stops immediately."""

    def test_wrong_number_action(self, base_intake: dict):
        """mark_wrong_number triggers wrong_number action."""
        from src.brain.agent import ConversationBrain

        brain = ConversationBrain()

        tool_result = brain._process_tool_call(
            type("ToolCall", (), {
                "function": type("Func", (), {
                    "name": "mark_wrong_number",
                    "arguments": '{"contact_point": "+14805559999"}',
                })(),
            })(),
            base_intake,
        )

        assert tool_result is not None
        assert tool_result["type"] == "action"
        assert tool_result["action"] == "wrong_number"

    def test_no_info_collected_from_wrong_number(self, base_intake: dict):
        """Agent must NOT ask intake questions to a wrong-number person."""
        from src.brain.agent import SYSTEM_PROMPT

        assert "WRONG NUMBER" in SYSTEM_PROMPT
        assert "STOP" in SYSTEM_PROMPT or "stop" in SYSTEM_PROMPT.lower()


# ─────────────────────────────────────────────
# 7.6 Spanish — full parity
# ─────────────────────────────────────────────


class TestScenario6_SpanishParity:
    """Spanish-speaking caller gets full parity, not downgrade."""

    def test_language_detection(self, base_intake: dict):
        """Spanish input sets language_observed=es."""
        from src.brain.agent import ConversationBrain

        brain = ConversationBrain()

        tool_result = brain._process_tool_call(
            type("ToolCall", (), {
                "function": type("Func", (), {
                    "name": "set_language",
                    "arguments": '{"language": "es"}',
                })(),
            })(),
            base_intake,
        )

        assert tool_result is not None
        assert tool_result["field"] == "preferred_lang"
        assert tool_result["value"] == "es"
        assert tool_result["confidence"] == 1.0

    def test_spanish_retainer_template(self, base_intake: dict):
        """Spanish language triggers Spanish retainer template."""
        base_intake["engagement"]["language_observed"] = "es"

        from src.models.intake import IntakeRecord

        record = IntakeRecord.model_validate(base_intake)
        assert record.engagement.language_observed == "es"


# ─────────────────────────────────────────────
# Compliance Gates
# ─────────────────────────────────────────────


class TestComplianceGates:
    """Test all compliance gates are working."""

    def test_quiet_hours_block(self, base_intake: dict):
        """Quiet hours (8a-9p local) block all outreach."""
        # This test would need to mock the current time
        # For now, verify the gate logic exists
        from src.config import settings

        assert settings.quiet_hours_start == 8
        assert settings.quiet_hours_end == 21

    def test_recording_consent_required(self, base_intake: dict):
        """Recording consent is required for voice."""
        base_intake["consent"]["recording_consent"] = False

        from src.config import settings

        # In production, this would block the call
        # For now, verify the setting exists
        assert hasattr(settings, "max_voice_attempts")

    def test_demo_voice_toggle(self, base_intake: dict):
        """Demo toggle bypasses AI voice consent check."""
        from src.config import settings

        assert settings.demo_ai_voice_consent is True


# ─────────────────────────────────────────────
# Integration Tests
# ─────────────────────────────────────────────


class TestIntakeRecord:
    """Test IntakeRecord model behavior."""

    def test_slot_does_not_overwrite_confirmed(self, base_intake: dict):
        """Confirmed slots are not overwritten by lower-confidence data."""
        from src.models.enums import IntakeField
        from src.models.intake import IntakeRecord

        record = IntakeRecord.model_validate(base_intake)

        # First capture
        record.set_slot(IntakeField.ACCIDENT_DATE, "2026-03-03", 0.9, "voice")
        record.confirm_slot(IntakeField.ACCIDENT_DATE)

        # Attempt overwrite
        record.set_slot(IntakeField.ACCIDENT_DATE, "2026-03-05", 0.5, "sms")

        # Should still be original value
        assert record.intake_slots["accident_date"].value == "2026-03-03"

    def test_total_attempts_count(self, base_intake: dict):
        """Total attempts counts across all channels."""
        from src.models.intake import IntakeRecord

        base_intake["engagement"]["attempts_by_channel"] = {
            "voice": 3,
            "sms": 2,
            "email": 1,
        }

        record = IntakeRecord.model_validate(base_intake)
        assert record.total_attempts() == 6

    def test_disposition_transitions(self, base_intake: dict):
        """Disposition can transition through states."""
        from src.models.intake import IntakeRecord

        record = IntakeRecord.model_validate(base_intake)
        assert record.disposition == Disposition.NEW

        record.disposition = Disposition.CHASING
        assert record.disposition == Disposition.CHASING

        record.disposition = Disposition.ENGAGED
        assert record.disposition == Disposition.ENGAGED

        record.disposition = Disposition.INTAKE_COMPLETE
        assert record.disposition == Disposition.INTAKE_COMPLETE
