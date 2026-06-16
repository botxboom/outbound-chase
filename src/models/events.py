from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from .enums import Channel, EventType


class Event(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    event_type: EventType
    lead_id: str
    channel: Channel | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    data: dict[str, Any] = Field(default_factory=dict)
    sequence: int = 0  # ordering within a lead

    def __str__(self) -> str:
        parts = [f"[{self.event_type.value}]"]
        if self.channel:
            parts.append(f"({self.channel.value})")
        if self.data:
            parts.append(str(self.data))
        return " ".join(parts)
