"""Dropbox Sign integration for retainer e-signatures."""
from __future__ import annotations

import httpx

from src.config import settings


async def send_retainer_for_signing(
    lead_id: str,
    language: str,
    channel: str,
    signer_name: str = "",
    signer_email: str = "",
) -> dict:
    """Send retainer document for e-signature via Dropbox Sign."""
    if not settings.dropbox_sign_api_key or settings.use_mock_channels:
        from src.db.models import AuditTrail, async_session

        envelope_id = f"mock-env-{lead_id}"
        async with async_session() as session:
            session.add(
                AuditTrail(
                    lead_id=lead_id,
                    action="mock.esign.send",
                    details={
                        "envelope_id": envelope_id,
                        "language": language,
                        "channel": channel,
                        "signer_name": signer_name,
                        "signer_email": signer_email,
                    },
                )
            )
            await session.commit()
        return {
            "success": True,
            "envelope_id": envelope_id,
            "sign_url": f"https://mock-sign.example/{envelope_id}",
            "error": None,
        }

    template_id = "retainer-es" if language == "es" else "retainer-en"

    payload = {
        "template_id": template_id,
        "title": "Representation Agreement",
        "subject": "Please sign your representation agreement",
        "message": "Thank you for choosing Marigold Injury Law.",
        "signers": [
            {
                "email_address": signer_email,
                "name": signer_name or "Client",
                "role": "Client",
            }
        ],
        "custom_fields": [{"name": "lead_id", "value": lead_id, "type": "text"}],
        "metadata": {"lead_id": lead_id, "language": language},
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                "https://api.hellosign.com/v3/signature_request/send_with_template",
                json=payload,
                auth=(settings.dropbox_sign_api_key, ""),
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            return {
                "success": True,
                "envelope_id": data.get("signature_request", {}).get("signature_request_id"),
                "sign_url": None,
                "error": None,
            }
        except httpx.HTTPError as e:
            return {"success": False, "error": str(e)}
