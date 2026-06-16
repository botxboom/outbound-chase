"""Shared test fixtures."""
from __future__ import annotations

import os

# Must be set before any src.db imports
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import httpx
import pytest
import pytest_asyncio

from src.models.enums import Channel, Disposition
from src.models.intake import ConsentRecord, EngagementState, IntakeRecord


@pytest.fixture
def base_intake() -> dict:
    return IntakeRecord(
        lead_id="test-lead-001",
        identity={
            "name": "David",
            "phone": ["+14805551234"],
            "email": ["david@example.com"],
        },
        consent=ConsentRecord(
            origin="inbound",
            channels_allowed=[Channel.VOICE, Channel.SMS, Channel.EMAIL],
            ai_voice_consent=True,
            recording_consent=True,
        ),
        engagement=EngagementState(),
        disposition=Disposition.NEW,
    ).model_dump(mode="json")


@pytest_asyncio.fixture
async def db_session():
    import src.config as config_module
    import src.db.models as db_models
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
    config_module.settings = config_module.Settings()
    db_models.engine = create_async_engine(config_module.settings.database_url, echo=False)
    db_models.async_session = async_sessionmaker(db_models.engine, expire_on_commit=False)
    await db_models.init_db()

    import src.store.projection as projection
    import src.channels.mock as mock_channels

    projection.async_session = db_models.async_session
    mock_channels.async_session = db_models.async_session

    yield


def llm_available() -> bool:
    base_url = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")
    try:
        r = httpx.get(base_url.rstrip("/") + "/models", timeout=5.0)
        return r.status_code < 500
    except Exception:
        try:
            url = base_url.rstrip("/").removesuffix("/v1") + "/api/tags"
            r = httpx.get(url, timeout=5.0)
            return r.status_code < 500
        except Exception:
            return False


@pytest.fixture
def require_llm():
    if not llm_available():
        pytest.skip("LLM endpoint not reachable — start Ollama or set LLM_BASE_URL")
