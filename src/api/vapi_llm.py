"""Vapi-compatible LLM endpoint.

Vapi sends conversation context to this endpoint during live calls.
Our brain processes it and returns the response.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.brain.agent import ConversationBrain
from src.db.models import async_session, LeadRecord
from sqlalchemy import select

app_vapi = FastAPI()
brain = ConversationBrain()


async def _fetch_lead_record(lead_id: str) -> dict:
    """Fetch lead intake record from DB."""
    if not lead_id:
        return {}
    try:
        async with async_session() as session:
            result = await session.execute(
                select(LeadRecord).where(LeadRecord.id == lead_id)
            )
            row = result.scalar_one_or_none()
            if row:
                return {
                    "lead_id": row.id,
                    "identity": row.intake_data.get("identity", {}),
                    "consent": row.consent_data,
                    "intake_slots": row.intake_data.get("intake_slots", {}),
                    "engagement": row.engagement_data,
                    "disposition": row.disposition,
                    "language": row.language,
                }
    except Exception:
        pass
    return {}


def _generate_dynamic_opener(intake_record: dict) -> str:
    """Generate a personalized first message based on lead context."""
    identity = intake_record.get("identity", {})
    name = identity.get("name")
    language = intake_record.get("language", "en")
    origin = intake_record.get("consent", {}).get("origin", "outbound")
    slots = intake_record.get("intake_slots", {})
    accident_date = slots.get("accident_date", {}).get("value")

    # Spanish opener
    if language == "es":
        if name:
            opener = (
                f"Hola, ¿es {name}? Soy el asistente automático de Marigold Injury Law. "
                "No soy abogado, y le informo que estas llamadas se graban por precisión. "
            )
        else:
            opener = (
                "Hola, soy el asistente automático de Marigold Injury Law. "
                "No soy abogado, y le informo que estas llamadas se graban por precisión. "
            )

        if origin == "inbound":
            opener += "Gracias por comunicarse con nosotros. "
        elif accident_date:
            opener += f"Estamos siguiendo su caso del accidente del {accident_date}. "
        else:
            opener += "Estamos siguiendo su caso de accidente. "

        opener += "¿Tiene dos minutos para que el abogado tenga toda la información?"
        return opener

    # English opener
    if name:
        opener = (
            f"Hi, is this {name}? This is the automated assistant with Marigold Injury Law — "
            "I'm not an attorney, and just so you know I record these calls for accuracy. "
        )
    else:
        opener = (
            "Hi, this is the automated assistant with Marigold Injury Law — "
            "I'm not an attorney, and just so you know I record these calls for accuracy. "
        )

    if origin == "inbound":
        opener += "Thanks for reaching out to us. "
    elif accident_date:
        opener += f"We're following up on your accident from {accident_date}. "
    else:
        opener += "We're following up on your accident. "

    opener += "Do you have two minutes so I can make sure the attorney has what they need?"
    return opener


@app_vapi.post("/v1/chat/completions")
async def chat_completions(request: Request) -> JSONResponse:
    """OpenAI-compatible chat completions endpoint for Vapi."""
    data = await request.json()

    messages = data.get("messages", [])
    model = data.get("model", "mimo-v2.5")

    # Extract lead context from Vapi metadata
    metadata = data.get("metadata", {})
    lead_id = metadata.get("lead_id", "")

    # Fetch lead record from DB (or use metadata)
    intake_record = await _fetch_lead_record(lead_id)
    if not intake_record:
        intake_record = {
            "lead_id": lead_id,
            "identity": metadata.get("identity", {}),
            "consent": metadata.get("consent", {}),
            "intake_slots": metadata.get("intake_slots", {}),
            "engagement": metadata.get("engagement", {}),
            "disposition": metadata.get("disposition", "ENGAGED"),
        }

    # Detect first turn (no prior messages or only system)
    prior_user_msgs = [m for m in messages if m.get("role") == "user"]
    is_first_turn = len(prior_user_msgs) == 0

    if is_first_turn:
        # Generate dynamic opener based on lead context
        content = _generate_dynamic_opener(intake_record)
    else:
        # Get the last user message
        user_message = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_message = msg.get("content", "")
                break

        # Run brain turn
        result = await brain.run_turn(
            intake_record=intake_record,
            channel="voice",
            lead_id=lead_id,
            inbound_message=user_message,
        )
        content = result.get("content", {}).get("text", "")

    # Format as OpenAI-compatible response
    response = {
        "id": f"chatcmpl-{lead_id}",
        "object": "chat.completion",
        "created": 1234567890,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }

    return JSONResponse(content=response)
