"""Channel adapters - thin normalization layer.

Each adapter converts normalized content to channel-specific format:
- Voice: SSML, barge-in config
- SMS: 160-char aware, no links (or short links)
- Email: subject + body, HTML

Inbound: transport → normalized InboundMsg
Outbound: intent → channel-specific format
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ChannelAdapter(ABC):
    """Base class for all channel adapters."""

    @abstractmethod
    async def send(self, lead_id: str, content: dict) -> dict:
        """Send content via this channel.

        Args:
            lead_id: Lead identifier
            content: {"text": "...", "type": "voice"|"sms"|"email"}

        Returns:
            {"success": bool, "message_id": str | None, "error": str | None}
        """
        ...

    @abstractmethod
    async def normalize_inbound(self, raw_data: dict) -> dict:
        """Convert raw inbound event to normalized format.

        Returns:
            {
                "lead_id": str,
                "channel": str,
                "message": str,
                "sender": str,
                "timestamp": str,
            }
        """
        ...

    @abstractmethod
    def render_outbound(self, content: dict, context: dict) -> Any:
        """Render content for this channel's format."""
        ...
