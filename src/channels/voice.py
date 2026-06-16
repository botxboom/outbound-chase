"""Voice channel adapter - Vapi integration."""
from __future__ import annotations

import httpx

from src.config import settings

from .base import ChannelAdapter


class VoiceAdapter(ChannelAdapter):
    """Voice channel via Vapi."""

    def __init__(self) -> None:
        self.api_key = settings.vapi_api_key
        self.phone_number_id = settings.vapi_phone_number_id
        self.base_url = "https://api.vapi.ai"

    async def send(self, lead_id: str, content: dict) -> dict:
        if not self.api_key:
            return {"success": False, "message_id": None, "error": "Vapi API key not configured"}

        phone_number = content.get("phone_number")
        if not phone_number:
            return {"success": False, "message_id": None, "error": "No phone number provided"}

        server_url = f"{settings.public_base_url.rstrip('/')}/v1/chat/completions"

        payload = {
            "phoneNumberId": self.phone_number_id,
            "customer": {"number": phone_number},
            "assistant": {
                "firstMessage": content.get("text", "Hello!"),
                "model": {
                    "provider": "custom-llm",
                    "url": server_url,
                    "model": settings.llm_model,
                    "metadata": {
                        "lead_id": lead_id,
                    },
                },
                "voice": {
                    "provider": "11labs",
                    "voiceId": "Rachel",
                },
            },
            "metadata": {"lead_id": lead_id},
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/call/phone",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()
                return {
                    "success": True,
                    "message_id": data.get("id"),
                    "amd_result": content.get("amd_result", "unknown"),
                    "error": None,
                }
            except httpx.HTTPError as e:
                return {"success": False, "message_id": None, "error": str(e)}

    async def normalize_inbound(self, raw_data: dict) -> dict:
        return {
            "lead_id": raw_data.get("metadata", {}).get("lead_id", ""),
            "channel": "voice",
            "message": raw_data.get("transcript", ""),
            "sender": raw_data.get("customer", {}).get("number", ""),
            "timestamp": raw_data.get("startedAt", ""),
            "event_type": raw_data.get("status", ""),
            "call_id": raw_data.get("id", ""),
        }

    def render_outbound(self, content: dict, context: dict) -> dict:
        return {
            "text": content.get("text", ""),
            "type": "voice",
            "phone_number": context.get("phone_number"),
            "amd_result": context.get("amd_result"),
        }
