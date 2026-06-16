"""FastAPI webhook server for inbound events."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from src.api.leads import router as leads_router, set_temporal_client
from src.config import settings

app = FastAPI(title="Outbound-Chase Webhooks", version="0.1.0")

from .vapi_llm import app_vapi

app.mount("/", app_vapi)

app.include_router(leads_router)

_temporal_client = None


@app.on_event("startup")
async def startup() -> None:
    global _temporal_client
    from src.db.models import init_db

    await init_db()

    try:
        import temporalio.client

        _temporal_client = await temporalio.client.Client.connect(
            f"{settings.temporal_host}:{settings.temporal_port}",
            namespace=settings.temporal_namespace,
        )
        set_temporal_client(_temporal_client)
    except Exception:
        _temporal_client = None


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "temporal": _temporal_client is not None}


@app.post("/webhooks/vapi")
async def vapi_webhook(request: Request) -> JSONResponse:
    data = await request.json()
    lead_id = data.get("metadata", {}).get("lead_id")
    if not lead_id:
        raise HTTPException(status_code=400, detail="Missing lead_id in metadata")

    if data.get("status") == "ended" and data.get("transcript"):
        await _signal_workflow(
            lead_id,
            "inbound_event",
            {
                "channel": "voice",
                "message": data.get("transcript", ""),
                "event_type": "call_ended",
            },
        )

    return JSONResponse({"received": True})


@app.post("/webhooks/sms")
async def sms_webhook(request: Request) -> PlainTextResponse:
    form_data = await request.form()
    data = dict(form_data)
    from_number = data.get("From", "")
    lead_id = await _find_lead_by_phone(from_number)

    if lead_id:
        await _signal_workflow(
            lead_id,
            "inbound_event",
            {
                "channel": "sms",
                "message": data.get("Body", ""),
                "sender": from_number,
                "event_type": "sms_inbound",
            },
        )

    return PlainTextResponse(
        '<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
        media_type="application/xml",
    )


@app.post("/webhooks/email")
async def email_webhook(request: Request) -> JSONResponse:
    data = await request.json()
    from_email = data.get("From", "").split("<")[-1].rstrip(">")
    lead_id = await _find_lead_by_email(from_email)

    if lead_id:
        await _signal_workflow(
            lead_id,
            "inbound_event",
            {
                "channel": "email",
                "message": data.get("TextBody", ""),
                "sender": from_email,
                "subject": data.get("Subject", ""),
                "event_type": "email_inbound",
            },
        )

    return JSONResponse({"received": True})


@app.post("/webhooks/esign")
async def esign_webhook(request: Request) -> JSONResponse:
    data = await request.json()
    event_type = data.get("event", {}).get("event_type", "")
    metadata = data.get("signature_request", {}).get("metadata", {})
    lead_id = metadata.get("lead_id")

    if lead_id and event_type == "signature_request_signed":
        await _signal_workflow(
            lead_id,
            "inbound_event",
            {
                "channel": "esign",
                "event_type": "retainer_signed",
                "envelope_id": data.get("signature_request", {}).get("signature_request_id"),
                "signed_pdf_ref": data.get("signature_request", {}).get("signed_pdf_url"),
            },
        )

    return JSONResponse({"received": True})


@app.post("/webhooks/sms/status")
async def sms_status_webhook(request: Request) -> JSONResponse:
    return JSONResponse({"received": True})


async def _signal_workflow(lead_id: str, signal_name: str, data: dict) -> None:
    if not _temporal_client:
        from src.orchestrator.activities import process_inbound

        await process_inbound(lead_id, data)
        return

    try:
        handle = _temporal_client.get_workflow_handle(f"chase-{lead_id}")
        await handle.signal(signal_name, data)
    except Exception:
        from src.orchestrator.workflow import OutboundChaseWorkflow
        from src.db.models import LeadRecord, async_session
        from sqlalchemy import select
        from src.store.projection import record_from_lead_row

        async with async_session() as session:
            result = await session.execute(select(LeadRecord).where(LeadRecord.id == lead_id))
            row = result.scalar_one_or_none()
            if row:
                record = record_from_lead_row(row)
                await _temporal_client.start_workflow(
                    OutboundChaseWorkflow.run,
                    id=f"chase-{lead_id}",
                    task_queue="outbound-chase",
                    args=[lead_id, record.model_dump(mode="json")],
                )
                handle = _temporal_client.get_workflow_handle(f"chase-{lead_id}")
                await handle.signal(signal_name, data)


async def _find_lead_by_phone(phone: str) -> str | None:
    from src.db.models import LeadRecord, async_session
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(select(LeadRecord))
        for row in result.scalars():
            phones = row.intake_data.get("identity", {}).get("phone", [])
            if phone in phones:
                return row.id
    return None


async def _find_lead_by_email(email: str) -> str | None:
    from src.db.models import LeadRecord, async_session
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(select(LeadRecord))
        for row in result.scalars():
            emails = row.intake_data.get("identity", {}).get("email", [])
            if email in emails:
                return row.id
    return None
