"""Clio Manage integration.

Write-back for Contact, Matter, Communications, Document, Note.
Mature REST API with free dev account.
"""
from __future__ import annotations

import httpx

from src.config import settings


class ClioClient:
    """Clio Manage API client."""

    def __init__(self) -> None:
        self.base_url = settings.clio_base_url
        self.access_token = settings.clio_access_token

    async def write_lead(
        self, intake_record: dict, lead_id: str, milestone: str | None = None
    ) -> dict:
        """Write lead data to Clio Manage."""
        if not self.access_token:
            from src.db.models import AuditTrail, async_session

            async with async_session() as session:
                session.add(
                    AuditTrail(
                        lead_id=lead_id,
                        action="mock.clio.write",
                        details={
                            "milestone": milestone,
                            "disposition": intake_record.get("disposition"),
                        },
                    )
                )
                await session.commit()
            return {"success": True, "mock": True, "milestone": milestone}

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        results = {}

        try:
            # 1. Create/update Contact
            contact_data = self._build_contact(intake_record)
            contact_result = await self._api_call("POST", "/contacts", headers, contact_data)
            contact_id = contact_result.get("data", {}).get("id")
            results["contact"] = contact_result

            # 2. Create Matter
            if contact_id:
                matter_data = self._build_matter(intake_record, contact_id)
                matter_result = await self._api_call("POST", "/matters", headers, matter_data)
                matter_id = matter_result.get("data", {}).get("id")
                results["matter"] = matter_result

                # 3. Log communications
                if matter_id:
                    comms = self._build_communications(intake_record, matter_id)
                    for comm in comms:
                        comm_result = await self._api_call(
                            "POST", f"/matters/{matter_id}/communications", headers, comm
                        )

                    # 4. Post note with SOL date
                    note_data = self._build_sol_note(intake_record, matter_id)
                    note_result = await self._api_call(
                        "POST", f"/matters/{matter_id}/notes", headers, note_data
                    )
                    results["note"] = note_result

            return {"success": True, "results": results}

        except Exception as e:
            return {"success": False, "error": str(e), "partial_results": results}

    async def _api_call(
        self, method: str, path: str, headers: dict, data: dict | None = None
    ) -> dict:
        """Make an API call to Clio with rate limit handling."""
        async with httpx.AsyncClient() as client:
            for attempt in range(3):
                try:
                    response = await client.request(
                        method,
                        f"{self.base_url}{path}",
                        json=data,
                        headers=headers,
                        timeout=30.0,
                    )

                    # Handle rate limits
                    if response.status_code == 429:
                        retry_after = int(response.headers.get("Retry-After", 60))
                        import asyncio
                        await asyncio.sleep(retry_after)
                        continue

                    response.raise_for_status()
                    return response.json()

                except httpx.HTTPError as e:
                    if attempt == 2:
                        raise
                    continue

        return {"error": "Max retries exceeded"}

    def _build_contact(self, intake_record: dict) -> dict:
        """Build Clio Contact from intake record."""
        identity = intake_record.get("identity", {})
        slots = intake_record.get("intake_slots", {})

        return {
            "data": {
                "name": identity.get("name", slots.get("name", {}).get("value", "Unknown")),
                "type": "Person",
                "phone_numbers": [
                    {"number": p, "type": "Mobile"}
                    for p in identity.get("phone", [])
                ] if identity.get("phone") else [],
                "email_addresses": [
                    {"address": e, "type": "Home"}
                    for e in identity.get("email", [])
                ] if identity.get("email") else [],
            }
        }

    def _build_matter(self, intake_record: dict, contact_id: int) -> dict:
        """Build Clio Matter from intake record."""
        slots = intake_record.get("intake_slots", {})

        return {
            "data": {
                "description": f"PI Case - {slots.get('accident_type', {}).get('value', 'Unknown')}",
                "status": "Open",
                "practice_area": {"id": 1},  # Personal Injury
                "responsible_attorney": {"id": 1},  # Default attorney
                "client": {"id": contact_id},
                "custom_field_values": [
                    {
                        "custom_field_id": 1,
                        "value": slots.get("accident_date", {}).get("value"),
                    },
                    {
                        "custom_field_id": 2,
                        "value": slots.get("accident_location", {}).get("value"),
                    },
                    {
                        "custom_field_id": 3,
                        "value": slots.get("injuries", {}).get("value"),
                    },
                    {
                        "custom_field_id": 4,
                        "value": slots.get("treatment_status", {}).get("value"),
                    },
                ],
            }
        }

    def _build_communications(self, intake_record: dict, matter_id: int) -> list[dict]:
        """Build communication log entries."""
        engagement = intake_record.get("engagement", {})
        comms = []

        # Log each attempt
        attempts = engagement.get("attempts_by_channel", {})
        for channel, count in attempts.items():
            for i in range(count):
                comms.append({
                    "data": {
                        "matter_id": matter_id,
                        "type": channel.upper(),
                        "direction": "Outbound",
                        "subject": f"{channel.title()} attempt #{i+1}",
                    }
                })

        return comms

    def _build_sol_note(self, intake_record: dict, matter_id: int) -> dict:
        """Build note with SOL date flagged for attorney."""
        slots = intake_record.get("intake_slots", {})
        accident_date = slots.get("accident_date", {}).get("value")

        note_text = "AUTOMATED INTAKE NOTE\n"
        note_text += "=" * 40 + "\n\n"
        note_text += "STATUTE OF LIMITATIONS: FLAGGED FOR ATTORNEY REVIEW\n"
        note_text += f"Accident Date: {accident_date}\n"
        note_text += "SOL Date: Derived by system — see custom field\n"
        note_text += "NOTE: System does NOT advise on SOL. Attorney must verify.\n\n"
        note_text += "INTAKE SLOTS:\n"
        for field, slot in slots.items():
            note_text += f"  {field}: {slot.get('value', 'N/A')} (confidence: {slot.get('confidence', 0)})\n"

        return {
            "data": {
                "matter_id": matter_id,
                "body": note_text,
            }
        }
