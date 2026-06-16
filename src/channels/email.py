"""Email channel adapter - Postmark integration."""
from __future__ import annotations

import httpx

from src.config import settings

from .base import ChannelAdapter


class EmailAdapter(ChannelAdapter):
    """Email channel via Postmark."""

    def __init__(self) -> None:
        self.server_token = settings.postmark_server_token
        self.from_email = settings.postmark_from_email
        self.from_name = settings.postmark_from_name
        self.base_url = "https://api.postmarkapp.com"

    async def send(self, lead_id: str, content: dict) -> dict:
        """Send email via Postmark."""
        if not self.server_token:
            return {"success": False, "message_id": None, "error": "Postmark not configured"}

        to_email = content.get("email")
        if not to_email:
            return {"success": False, "message_id": None, "error": "No email address"}

        subject = content.get("subject", "Marigold Injury Law - Follow Up")
        body = content.get("text", "")
        html_body = content.get("html", self._text_to_html(body))

        payload = {
            "From": f"{self.from_name} <{self.from_email}>",
            "To": to_email,
            "Subject": subject,
            "TextBody": body,
            "HtmlBody": html_body,
            "TrackOpens": True,
            "TrackLinks": "HtmlAndText",
            "Metadata": {"lead_id": lead_id},
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/email",
                    json=payload,
                    headers={
                        "Accept": "application/json",
                        "X-Postmark-Server-Token": self.server_token,
                    },
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()
                return {
                    "success": data.get("ErrorCode") == 0,
                    "message_id": data.get("MessageID"),
                    "error": data.get("Message"),
                }
            except httpx.HTTPError as e:
                return {"success": False, "message_id": None, "error": str(e)}

    async def normalize_inbound(self, raw_data: dict) -> dict:
        """Normalize Postmark inbound webhook to standard format."""
        return {
            "lead_id": raw_data.get("metadata", {}).get("lead_id", ""),
            "channel": "email",
            "message": raw_data.get("TextBody", raw_data.get("HtmlBody", "")),
            "sender": raw_data.get("From", ""),
            "timestamp": raw_data.get("ReceivedAt", ""),
            "subject": raw_data.get("Subject", ""),
            "message_id": raw_data.get("MessageID", ""),
        }

    def render_outbound(self, content: dict, context: dict) -> dict:
        """Render for email channel."""
        return {
            "text": content.get("text", ""),
            "subject": content.get("subject", "Marigold Injury Law - Follow Up"),
            "html": content.get("html", ""),
            "type": "email",
            "email": context.get("email"),
        }

    def _text_to_html(self, text: str) -> str:
        """Convert plain text to basic HTML."""
        paragraphs = text.split("\n\n")
        html_parts = []
        for p in paragraphs:
            if p.strip():
                html_parts.append(f"<p>{p.strip()}</p>")
        return "<html><body>" + "\n".join(html_parts) + "</body></html>"
