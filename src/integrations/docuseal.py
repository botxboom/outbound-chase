"""DocuSeal eSign integration (self-hosted or cloud)."""
from __future__ import annotations

import httpx

from src.config import settings


def _api_base() -> str:
    return settings.docuseal_api_url.rstrip("/")


def _template_id(language: str) -> int:
    if language == "es" and settings.docuseal_template_id_es:
        return settings.docuseal_template_id_es
    return settings.docuseal_template_id_en


def _sign_url_from_submitter(submitter: dict) -> str | None:
    if submitter.get("embed_src"):
        return str(submitter["embed_src"])
    slug = submitter.get("slug")
    if slug:
        return f"{_api_base()}/s/{slug}"
    return None


async def send_retainer_for_signing(
    lead_id: str,
    language: str,
    channel: str,
    signer_name: str = "",
    signer_email: str = "",
) -> dict:
    """Create a DocuSeal submission for retainer e-signature."""
    if not settings.docuseal_api_key or settings.use_mock_channels:
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

    template_id = _template_id(language)
    if not template_id:
        return {
            "success": False,
            "error": "DOCUSEAL_TEMPLATE_ID_EN not configured — create a template in DocuSeal UI",
        }

    role = settings.docuseal_submitter_role
    submitter: dict = {
        "role": role,
        "name": signer_name or "Client",
        "external_id": lead_id,
        "metadata": {"lead_id": lead_id, "language": language},
        "send_email": bool(signer_email),
    }
    if signer_email:
        submitter["email"] = signer_email

    payload = {
        "template_id": template_id,
        "send_email": bool(signer_email),
        "submitters": [submitter],
        "metadata": {"lead_id": lead_id, "language": language},
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Auth-Token": settings.docuseal_api_key,
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{_api_base()}/api/submissions",
                json=payload,
                headers=headers,
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as e:
            detail = str(e)
            if hasattr(e, "response") and e.response is not None:
                detail = e.response.text or detail
            return {"success": False, "error": detail}

    # Response may be a list of submitters or an object with submitters
    submitters = data if isinstance(data, list) else data.get("submitters", [data])
    first = submitters[0] if submitters else {}
    submission_id = first.get("submission_id") or data.get("id") if isinstance(data, dict) else None
    envelope_id = str(submission_id or first.get("id") or lead_id)
    sign_url = _sign_url_from_submitter(first)

    return {
        "success": True,
        "envelope_id": envelope_id,
        "sign_url": sign_url,
        "error": None,
    }
