"""Email channel adapter — Resend integration."""
from __future__ import annotations

import httpx

from src.config import settings

from .base import ChannelAdapter


class EmailAdapter(ChannelAdapter):
    """Email channel via Resend (https://resend.com)."""

    def __init__(self) -> None:
        self.api_key = settings.resend_api_key
        self.from_email = settings.email_from_email
        self.from_name = settings.email_from_name
        self.base_url = "https://api.resend.com"

    def _from_header(self) -> str:
        if self.from_name:
            return f"{self.from_name} <{self.from_email}>"
        return self.from_email

    async def send(self, lead_id: str, content: dict) -> dict:
        """Send email via Resend."""
        if not self.api_key:
            return {"success": False, "message_id": None, "error": "Resend not configured"}

        to_email = content.get("email")
        if not to_email:
            return {"success": False, "message_id": None, "error": "No email address"}

        subject = content.get("subject", "Marigold Injury Law - Follow Up")
        body = content.get("text", "")
        html_body = content.get("html", self._text_to_html(body))

        payload = {
            "from": self._from_header(),
            "to": [to_email],
            "subject": subject,
            "text": body,
            "html": html_body,
            "tags": [{"name": "lead_id", "value": lead_id}],
        }

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/emails",
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
                    "error": None,
                }
            except httpx.HTTPError as e:
                detail = str(e)
                if hasattr(e, "response") and e.response is not None:
                    detail = e.response.text or detail
                return {"success": False, "message_id": None, "error": detail}

    async def normalize_inbound(self, raw_data: dict) -> dict:
        """Normalize inbound email webhook (generic / future Resend inbound)."""
        tags = {t.get("name"): t.get("value") for t in raw_data.get("tags", []) if isinstance(t, dict)}
        return {
            "lead_id": tags.get("lead_id", raw_data.get("lead_id", "")),
            "channel": "email",
            "message": raw_data.get("text", raw_data.get("html", "")),
            "sender": raw_data.get("from", ""),
            "timestamp": raw_data.get("created_at", ""),
            "subject": raw_data.get("subject", ""),
            "message_id": raw_data.get("id", ""),
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
