from .base import ChannelAdapter
from .email import EmailAdapter
from .mock import MockEmailAdapter, MockSMSAdapter, MockVoiceAdapter
from .sms import SMSAdapter
from .voice import VoiceAdapter
from .voice_simulated import SimulatedVoiceAdapter

from src.config import settings

_adapters: dict[str, ChannelAdapter] = {}


def _use_mock(channel: str) -> bool:
    if settings.use_mock_channels:
        return True
    if channel == "voice" and settings.voice_mode == "simulated":
        return True
    if channel == "sms" and not settings.twilio_account_sid:
        return True
    if channel == "email" and not settings.resend_api_key:
        return True
    if channel == "voice" and not settings.vapi_api_key:
        return True
    return False


def get_adapter(channel: str) -> ChannelAdapter:
    """Get or create a channel adapter."""
    if channel not in _adapters:
        if channel == "voice":
            if settings.voice_mode == "simulated" or _use_mock("voice"):
                _adapters[channel] = SimulatedVoiceAdapter()
            else:
                _adapters[channel] = VoiceAdapter()
        elif channel == "sms":
            _adapters[channel] = MockSMSAdapter() if _use_mock("sms") else SMSAdapter()
        elif channel == "email":
            _adapters[channel] = MockEmailAdapter() if _use_mock("email") else EmailAdapter()
        else:
            raise ValueError(f"Unknown channel: {channel}")
    return _adapters[channel]


def reset_adapters() -> None:
    """Clear adapter cache (for tests)."""
    _adapters.clear()
    MockSMSAdapter.clear()
    MockEmailAdapter.clear()
    MockVoiceAdapter.clear()


__all__ = [
    "ChannelAdapter",
    "EmailAdapter",
    "MockEmailAdapter",
    "MockSMSAdapter",
    "MockVoiceAdapter",
    "SMSAdapter",
    "SimulatedVoiceAdapter",
    "VoiceAdapter",
    "get_adapter",
    "reset_adapters",
]
