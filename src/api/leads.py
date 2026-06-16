"""Lead ingestion and simulation API."""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.models.enums import Channel, Disposition
from src.store.projection import create_lead, load_lead_record, rebuild_projection

router = APIRouter(prefix="/api/leads", tags=["leads"])

_temporal_client = None


def set_temporal_client(client: Any) -> None:
    global _temporal_client
    _temporal_client = client


class CreateLeadRequest(BaseModel):
    identity: dict[str, Any] = Field(default_factory=dict)
    consent: dict[str, Any] | None = None
    intake_slots: dict[str, Any] | None = None
    trigger: str = "web_form"
    lead_id: str | None = None


class SimulateInboundRequest(BaseModel):
    channel: str = "sms"
    message: str
    sender: str | None = None


class SimulateVoiceRequest(BaseModel):
    amd_result: str = "human"
    caller_messages: list[str] = Field(default_factory=list)


async def _start_workflow(lead_id: str, record: dict) -> None:
    if not _temporal_client:
        return
    from src.orchestrator.workflow import OutboundChaseWorkflow

    try:
        await _temporal_client.start_workflow(
            OutboundChaseWorkflow.run,
            args=[lead_id, record],
            id=f"chase-{lead_id}",
            task_queue="outbound-chase",
        )
    except Exception:
        # Workflow may already exist
        handle = _temporal_client.get_workflow_handle(f"chase-{lead_id}")
        await handle.signal("inbound_event", {"channel": "sms", "message": "restart"})


@router.post("")
async def create_lead_endpoint(body: CreateLeadRequest) -> dict:
    lead_id = body.lead_id or f"lead-{uuid4().hex[:12]}"
    record = await create_lead(
        lead_id=lead_id,
        identity=body.identity,
        consent=body.consent,
        intake_slots=body.intake_slots,
        trigger=body.trigger,
    )
    await _start_workflow(lead_id, record.model_dump(mode="json"))
    return {"lead_id": lead_id, "disposition": record.disposition.value}


@router.get("/{lead_id}")
async def get_lead(lead_id: str) -> dict:
    record = await load_lead_record(lead_id)
    if not record:
        raise HTTPException(status_code=404, detail="Lead not found")
    return record.model_dump(mode="json")


@router.post("/{lead_id}/simulate/inbound")
async def simulate_inbound(lead_id: str, body: SimulateInboundRequest) -> dict:
    record = await load_lead_record(lead_id)
    if not record:
        raise HTTPException(status_code=404, detail="Lead not found")

    if _temporal_client:
        handle = _temporal_client.get_workflow_handle(f"chase-{lead_id}")
        await handle.signal(
            "inbound_event",
            {
                "channel": body.channel,
                "message": body.message,
                "sender": body.sender or record.identity.get("phone", [""])[0],
                "event_type": f"{body.channel}_inbound",
            },
        )
        return {"signaled": True, "lead_id": lead_id}

    # Direct processing without Temporal (dev fallback)
    from src.orchestrator.activities import process_inbound

    result = await process_inbound(
        lead_id,
        {
            "channel": body.channel,
            "message": body.message,
            "sender": body.sender,
            "event_type": f"{body.channel}_inbound",
        },
    )
    return {"signaled": False, "processed": True, "result": result}


@router.post("/{lead_id}/simulate/voice")
async def simulate_voice(lead_id: str, body: SimulateVoiceRequest) -> dict:
    record = await load_lead_record(lead_id)
    if not record:
        raise HTTPException(status_code=404, detail="Lead not found")

    from src.channels.mock import MockVoiceAdapter
    from src.orchestrator.activities import handle_voice_outcome, process_inbound

    MockVoiceAdapter.set_amd(lead_id, body.amd_result)

    if body.amd_result == "human" and body.caller_messages:
        outcome = await handle_voice_outcome(
            lead_id,
            record.model_dump(mode="json"),
            "human",
            "call_attempt",
        )
        transcript = []
        for msg in body.caller_messages:
            result = await process_inbound(
                lead_id,
                {"channel": "voice", "message": msg, "event_type": "call_ended"},
            )
            transcript.append({"caller": msg, "agent": result.get("content", {})})
        return {"outcome": outcome, "transcript": transcript}

    outcome = await handle_voice_outcome(
        lead_id,
        record.model_dump(mode="json"),
        body.amd_result,
        "call_attempt",
    )
    return {"outcome": outcome}


@router.post("/{lead_id}/simulate/esign-signed")
async def simulate_esign_signed(lead_id: str) -> dict:
    if _temporal_client:
        handle = _temporal_client.get_workflow_handle(f"chase-{lead_id}")
        await handle.signal(
            "inbound_event",
            {
                "channel": "esign",
                "event_type": "retainer_signed",
                "envelope_id": f"mock-env-{lead_id}",
                "signed_pdf_ref": f"mock-pdf-{lead_id}.pdf",
            },
        )
        return {"signaled": True}

    from src.orchestrator.activities import process_inbound

    result = await process_inbound(
        lead_id,
        {
            "channel": "esign",
            "event_type": "retainer_signed",
            "envelope_id": f"mock-env-{lead_id}",
            "signed_pdf_ref": f"mock-pdf-{lead_id}.pdf",
        },
    )
    return {"processed": True, "result": result}


@router.post("/{lead_id}/rebuild")
async def rebuild_lead_projection(lead_id: str) -> dict:
    record = await rebuild_projection(lead_id)
    if not record:
        raise HTTPException(status_code=404, detail="Lead not found")
    return record.model_dump(mode="json")
