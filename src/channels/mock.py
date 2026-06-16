"""Mock channel adapters for local dev and CI."""
from __future__ import annotations

import json
from typing import Any

from src.db.models import AuditTrail, async_session

from .base import ChannelAdapter


async def _audit(lead_id: str, action: str, details: dict[str, Any]) -> None:
    async with async_session() as session:
        session.add(AuditTrail(lead_id=lead_id, action=action, details=details))
        await session.commit()


class MockSMSAdapter(ChannelAdapter):
    sent_messages: list[dict[str, Any]] = []

    async def send(self, lead_id: str, content: dict) -> dict:
        msg = {
            "lead_id": lead_id,
            "channel": "sms",
            "text": content.get("text", ""),
            "phone_number": content.get("phone_number"),
        }
        MockSMSAdapter.sent_messages.append(msg)
        await _audit(lead_id, "mock.sms.send", msg)
        return {"success": True, "message_id": f"mock-sms-{len(self.sent_messages)}", "error": None}

    async def normalize_inbound(self, raw_data: dict) -> dict:
        return {
            "lead_id": raw_data.get("lead_id", ""),
            "channel": "sms",
            "message": raw_data.get("Body", raw_data.get("message", "")),
            "sender": raw_data.get("From", raw_data.get("sender", "")),
            "timestamp": raw_data.get("timestamp", ""),
        }

    def render_outbound(self, content: dict, context: dict) -> dict:
        return {"text": content.get("text", ""), "phone_number": context.get("phone_number")}

    @classmethod
    def clear(cls) -> None:
        cls.sent_messages = []


class MockEmailAdapter(ChannelAdapter):
    sent_messages: list[dict[str, Any]] = []

    async def send(self, lead_id: str, content: dict) -> dict:
        msg = {
            "lead_id": lead_id,
            "channel": "email",
            "subject": content.get("subject", "Your Marigold Injury Law file"),
            "text": content.get("text", ""),
            "email": content.get("email"),
        }
        MockEmailAdapter.sent_messages.append(msg)
        await _audit(lead_id, "mock.email.send", msg)
        return {"success": True, "message_id": f"mock-email-{len(self.sent_messages)}", "error": None}

    async def normalize_inbound(self, raw_data: dict) -> dict:
        return {
            "lead_id": raw_data.get("lead_id", ""),
            "channel": "email",
            "message": raw_data.get("TextBody", raw_data.get("message", "")),
            "sender": raw_data.get("From", raw_data.get("sender", "")),
            "timestamp": raw_data.get("timestamp", ""),
        }

    def render_outbound(self, content: dict, context: dict) -> dict:
        return {
            "subject": content.get("subject", "Your Marigold Injury Law file"),
            "text": content.get("text", ""),
            "email": context.get("email"),
        }

    @classmethod
    def clear(cls) -> None:
        cls.sent_messages = []


class MockVoiceAdapter(ChannelAdapter):
    """Simulated voice — no PSTN."""

    call_log: list[dict[str, Any]] = []
    amd_results: dict[str, str] = {}

    async def send(self, lead_id: str, content: dict) -> dict:
        amd = content.get("amd_result") or self.amd_results.get(lead_id, "machine")
        call = {
            "lead_id": lead_id,
            "channel": "voice",
            "amd_result": amd,
            "phone_number": content.get("phone_number"),
            "text": content.get("text", ""),
            "voicemail_attempt": content.get("voicemail_attempt", 1),
        }
        MockVoiceAdapter.call_log.append(call)
        await _audit(lead_id, "mock.voice.call", call)
        return {
            "success": True,
            "message_id": f"mock-call-{len(self.call_log)}",
            "amd_result": amd,
            "error": None,
        }

    async def normalize_inbound(self, raw_data: dict) -> dict:
        return {
            "lead_id": raw_data.get("metadata", {}).get("lead_id", raw_data.get("lead_id", "")),
            "channel": "voice",
            "message": raw_data.get("transcript", raw_data.get("message", "")),
            "sender": raw_data.get("customer", {}).get("number", ""),
            "timestamp": raw_data.get("startedAt", ""),
            "amd_result": raw_data.get("amd_result"),
        }

    def render_outbound(self, content: dict, context: dict) -> dict:
        return {
            "text": content.get("text", ""),
            "phone_number": context.get("phone_number"),
            "amd_result": context.get("amd_result"),
            "voicemail_attempt": context.get("voicemail_attempt", 1),
        }

    @classmethod
    def clear(cls) -> None:
        cls.call_log = []
        cls.amd_results = {}

    @classmethod
    def set_amd(cls, lead_id: str, result: str) -> None:
        cls.amd_results[lead_id] = result
