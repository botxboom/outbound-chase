"""Clio Grow stub — read partial leads from fixture file."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.config import settings
from src.store.projection import create_lead

_processed: set[str] = set()


def _fixture_path() -> Path:
    return Path(settings.clio_grow_fixture_path)


def poll_new_leads() -> list[dict[str, Any]]:
    """Return unprocessed partial leads from fixture file."""
    path = _fixture_path()
    if not path.exists():
        return []

    with path.open() as f:
        leads = json.load(f)

    return [lead for lead in leads if lead.get("external_id") not in _processed]


async def ingest_fixture_leads() -> list[str]:
    """Create leads from fixture and mark processed."""
    created = []
    for partial in poll_new_leads():
        external_id = partial.get("external_id", uuid4().hex)
        lead_id = f"grow-{external_id}"
        await create_lead(
            lead_id=lead_id,
            identity=partial.get("identity", {}),
            consent=partial.get("consent"),
            intake_slots=partial.get("intake_slots"),
            trigger="clio_grow_stub",
        )
        _processed.add(external_id)
        created.append(lead_id)
    return created


async def _main() -> None:
    from src.db.models import init_db

    await init_db()
    created = await ingest_fixture_leads()
    print(f"Ingested {len(created)} leads: {created}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Poll Clio Grow stub fixture")
    parser.parse_args()
    import asyncio

    asyncio.run(_main())
