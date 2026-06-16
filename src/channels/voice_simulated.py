"""Simulated voice adapter — drives multi-turn without Vapi PSTN."""
from __future__ import annotations

from typing import Any

from src.brain.agent import ConversationBrain
from src.channels.mock import MockVoiceAdapter


class SimulatedVoiceAdapter(MockVoiceAdapter):
    """Voice via simulated AMD + brain turns."""

    def __init__(self) -> None:
        self.brain = ConversationBrain()

    async def run_conversation_turn(
        self,
        intake_record: dict,
        lead_id: str,
        inbound_message: str | None = None,
        context: str | None = None,
    ) -> dict[str, Any]:
        """Run one brain turn for simulated voice."""
        extra_context = context or ""
        if extra_context:
            intake_record = {**intake_record, "_voice_context": extra_context}
        return await self.brain.run_turn(
            intake_record=intake_record,
            channel="voice",
            lead_id=lead_id,
            inbound_message=inbound_message,
        )
