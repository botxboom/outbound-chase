"""Unit tests for adaptive strategy layer."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.models.enums import Channel, Disposition, EngagementMode, MessageGoal, Responsiveness
from src.models.intake import ConsentRecord, IntakeRecord
from src.orchestrator.strategy import (
    TouchPlan,
    apply_signal_patch,
    extract_signals_rules,
    plan_initial_touch,
    plan_next_touch,
)


@pytest.fixture
def base_record() -> IntakeRecord:
    return IntakeRecord(
        lead_id="strat-001",
        identity={"name": "David", "phone": ["+14805551234"], "email": ["david@example.com"]},
        consent=ConsentRecord(
            origin="outbound",
            channels_allowed=[Channel.VOICE, Channel.SMS, Channel.EMAIL],
            ai_voice_consent=True,
        ),
    )


class TestSignalExtraction:
    def test_sms_inbound_marks_hot(self, base_record: IntakeRecord):
        patch = extract_signals_rules(
            base_record,
            {"direction": "inbound", "channel": "sms", "message": "Hi yes I was in an accident"},
        )
        assert patch.responsiveness.get("sms") == Responsiveness.HOT.value
        assert patch.channel_preference == Channel.SMS
        assert patch.engagement_mode == EngagementMode.ENGAGED

    def test_stop_calling_sets_do_not_call(self, base_record: IntakeRecord):
        patch = extract_signals_rules(
            base_record,
            {"direction": "inbound", "channel": "sms", "message": "Stop calling me, text only"},
        )
        assert patch.do_not_call is True
        assert patch.channel_preference == Channel.SMS

    def test_hostile_message(self, base_record: IntakeRecord):
        patch = extract_signals_rules(
            base_record,
            {"direction": "inbound", "channel": "voice", "message": "How the hell did you get my number?"},
        )
        assert patch.engagement_mode == EngagementMode.HOSTILE_OPEN

    def test_outbound_ghosting_detection(self, base_record: IntakeRecord):
        base_record.engagement.attempts_by_channel = {Channel.VOICE: 2, Channel.SMS: 1}
        patch = extract_signals_rules(base_record, {"direction": "outbound", "channel": "voice"})
        assert patch.engagement_mode == EngagementMode.GHOSTING


class TestPlanner:
    def test_sms_hot_never_voice(self, base_record: IntakeRecord):
        base_record.engagement.responsiveness = {Channel.SMS.value: Responsiveness.HOT.value}
        base_record.engagement.channel_preference = Channel.SMS
        base_record.disposition = Disposition.ENGAGED

        plan = plan_next_touch(base_record)
        assert plan.channel != Channel.VOICE
        assert plan.channel == Channel.SMS
        assert "sms" in plan.reason.lower() or "no_voice" in plan.reason

    def test_do_not_call_excludes_voice(self, base_record: IntakeRecord):
        base_record.engagement.do_not_call = True
        base_record.disposition = Disposition.CHASING

        plan = plan_next_touch(base_record)
        assert plan.channel != Channel.VOICE

    def test_hostile_deescalation_sms(self, base_record: IntakeRecord):
        base_record.engagement.engagement_mode = EngagementMode.HOSTILE_OPEN
        base_record.disposition = Disposition.ENGAGED

        plan = plan_next_touch(base_record)
        assert plan.channel == Channel.SMS
        assert plan.message_goal == MessageGoal.EMPATHY_CHECK_IN
        assert plan.wait_seconds >= 172800

    def test_ghosting_uses_cadence_fallback(self, base_record: IntakeRecord):
        base_record.engagement.engagement_mode = EngagementMode.GHOSTING
        base_record.engagement.cadence_step_index = 1
        base_record.disposition = Disposition.CHASING

        plan = plan_next_touch(base_record)
        assert "cadence_fallback" in plan.reason

    def test_chase_timeout_gives_up(self, base_record: IntakeRecord):
        base_record.created_at = datetime.now(timezone.utc) - timedelta(days=15)
        plan = plan_next_touch(base_record)
        assert plan.terminal_action == "give_up"

    def test_gate_retry_excludes_voice(self, base_record: IntakeRecord):
        base_record.engagement.cadence_step_index = 1
        plan = plan_next_touch(base_record, exclude_channel=Channel.VOICE)
        assert plan.channel != Channel.VOICE


class TestIngressPlans:
    def test_inbound_missed_call(self, base_record: IntakeRecord):
        base_record.consent.origin = "inbound"
        plan = plan_initial_touch(base_record)
        assert plan.message_goal == MessageGoal.INSTANT_REPLY
        assert plan.channel == Channel.SMS

    def test_web_form_partial(self, base_record: IntakeRecord):
        from src.models.enums import IntakeField

        base_record.set_slot(IntakeField.ACCIDENT_DATE, "2026-03-01", 0.8, "web_form")
        plan = plan_initial_touch(base_record, trigger="web_form")
        assert plan.message_goal == MessageGoal.CONFIRM_SLOTS

    def test_web_form_empty(self, base_record: IntakeRecord):
        plan = plan_initial_touch(base_record, trigger="web_form")
        assert plan.message_goal == MessageGoal.WARM_INTRO

    def test_clio_grow_soft_reengage(self, base_record: IntakeRecord):
        plan = plan_initial_touch(base_record, trigger="clio_grow_stub")
        assert plan.message_goal == MessageGoal.SOFT_REENGAGE
        assert plan.wait_seconds == 86400


class TestApplySignalPatch:
    def test_merges_responsiveness(self, base_record: IntakeRecord):
        from src.orchestrator.strategy import SignalPatch

        patch = SignalPatch(responsiveness={"sms": "hot"}, do_not_call=True)
        apply_signal_patch(base_record, patch)
        assert base_record.engagement.responsiveness["sms"] == "hot"
        assert base_record.engagement.do_not_call is True


class TestTouchPlanSerialization:
    def test_round_trip(self):
        plan = TouchPlan(
            channel=Channel.SMS,
            message_goal=MessageGoal.GAP_FILL,
            wait_seconds=3600,
            reason="test",
            constraints=["under 300 chars"],
        )
        restored = TouchPlan.from_dict(plan.to_dict())
        assert restored.channel == Channel.SMS
        assert restored.message_goal == MessageGoal.GAP_FILL
