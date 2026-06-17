"""Hybrid signal extraction — rules first, optional LLM for ambiguous messages."""
from __future__ import annotations

import json
from typing import Any

from src.brain.llm import chat_completion
from src.config import settings
from src.models.enums import Channel, EngagementMode
from src.models.intake import IntakeRecord
from src.orchestrator.strategy import SignalPatch, apply_signal_patch, extract_signals_rules


async def extract_signals_llm(record: IntakeRecord, message: str) -> SignalPatch:
    """LLM extraction for non-trivial inbound messages."""
    patch = SignalPatch()
    if not message or len(message.strip()) < 10:
        return patch

    prompt = f"""Analyze this lead message for intake chase strategy. Return JSON only.
Message: {message!r}

Fields (use null if unknown):
- channel_preference: "sms" | "voice" | "email" | null
- do_not_call: boolean
- contact_window: "morning" | "evening" | "afternoon" | "after_5pm" | null
- objections: list of strings e.g. privacy, has_lawyer
- engagement_mode: "engaged" | "hostile_open" | "slow_burn" | null
"""

    try:
        response = await chat_completion(
            messages=[
                {"role": "system", "content": "Return valid JSON only, no markdown."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=256,
            temperature=0,
        )
        content = (response.content or "").strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0]
        data = json.loads(content)
    except Exception:
        return patch

    if data.get("channel_preference"):
        try:
            patch.channel_preference = Channel(data["channel_preference"])
        except ValueError:
            pass
    if data.get("do_not_call") is True:
        patch.do_not_call = True
    if data.get("contact_window"):
        patch.contact_window = str(data["contact_window"])
    if data.get("engagement_mode"):
        try:
            patch.engagement_mode = EngagementMode(data["engagement_mode"])
        except ValueError:
            pass
    for obj in data.get("objections") or []:
        if obj and obj not in patch.objections:
            patch.objections.append(str(obj))
    return patch


def merge_patches(rule_patch: SignalPatch, llm_patch: SignalPatch) -> SignalPatch:
    """Merge LLM patch into rules; rules win on compliance-critical fields."""
    merged = SignalPatch()
    merged.responsiveness = {**llm_patch.responsiveness, **rule_patch.responsiveness}
    merged.channel_preference = rule_patch.channel_preference or llm_patch.channel_preference
    merged.do_not_call = rule_patch.do_not_call if rule_patch.do_not_call is not None else llm_patch.do_not_call
    merged.contact_window = rule_patch.contact_window or llm_patch.contact_window
    merged.engagement_mode = rule_patch.engagement_mode or llm_patch.engagement_mode
    merged.sentiment = rule_patch.sentiment or llm_patch.sentiment
    merged.objections = list(dict.fromkeys(rule_patch.objections + llm_patch.objections))
    merged.last_inbound_at = rule_patch.last_inbound_at or llm_patch.last_inbound_at
    merged.last_inbound_latency_sec = rule_patch.last_inbound_latency_sec or llm_patch.last_inbound_latency_sec
    return merged


async def extract_signals_hybrid(
    record: IntakeRecord,
    event_context: dict[str, Any],
) -> SignalPatch:
    """Rules first; optional LLM for inbound messages."""
    rule_patch = extract_signals_rules(record, event_context)
    message = event_context.get("message") or ""
    direction = event_context.get("direction", "inbound")

    use_llm = (
        settings.strategy_llm_signals
        and direction == "inbound"
        and len(message.strip()) > 20
    )
    if not use_llm:
        return rule_patch

    llm_patch = await extract_signals_llm(record, message)
    return merge_patches(rule_patch, llm_patch)


async def extract_and_apply_signals(
    record: IntakeRecord,
    event_context: dict[str, Any],
) -> SignalPatch:
    """Extract signals and apply to record in-memory."""
    patch = await extract_signals_hybrid(record, event_context)
    apply_signal_patch(record, patch)
    return patch
