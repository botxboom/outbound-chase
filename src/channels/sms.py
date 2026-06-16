"""SMS channel adapter - Twilio integration."""
from __future__ import annotations

from twilio.rest import Client as TwilioClient

from src.config import settings

from .base import ChannelAdapter


class SMSAdapter(ChannelAdapter):
    """SMS channel via Twilio."""

    def __init__(self) -> None:
        self.client = (
            TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)
            if settings.twilio_account_sid
            else None
        )
        self.from_number = settings.twilio_phone_number

    async def send(self, lead_id: str, content: dict) -> dict:
        """Send SMS via Twilio."""
        if not self.client:
            return {"success": False, "message_id": None, "error": "Twilio not configured"}

        to_number = content.get("phone_number")
        if not to_number:
            return {"success": False, "message_id": None, "error": "No phone number"}

        text = content.get("text", "")
        # Truncate to 1600 chars (SMS segment limit)
        if len(text) > 1600:
            text = text[:1597] + "..."

        try:
            message = self.client.messages.create(
                body=text,
                from_=self.from_number,
                to=to_number,
                status_callback=f"{settings.llm_base_url}/webhooks/sms/status",
            )
            return {"success": True, "message_id": message.sid, "error": None}
        except Exception as e:
            return {"success": False, "message_id": None, "error": str(e)}

    async def normalize_inbound(self, raw_data: dict) -> dict:
        """Normalize Twilio webhook to standard format."""
        return {
            "lead_id": raw_data.get("lead_id", ""),
            "channel": "sms",
            "message": raw_data.get("Body", ""),
            "sender": raw_data.get("From", ""),
            "timestamp": raw_data.get("DateSent", ""),
            "message_sid": raw_data.get("MessageSid", ""),
        }

    def render_outbound(self, content: dict, context: dict) -> dict:
        """Render for SMS channel."""
        text = content.get("text", "")
        # SMS doesn't support links well in some carriers
        if not context.get("allow_links"):
            # Strip markdown links
            import re
            text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
        return {
            "text": text,
            "type": "sms",
            "phone_number": context.get("phone_number"),
        }
