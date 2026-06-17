"""Conversation Brain - the LLM-powered agent.

A goal-directed slot-filler with a stable persona, sentiment adaptivity,
and hard UPL guardrails. Channel-blind: emits intent + content.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from src.brain.llm import chat_completion
from src.brain.upl_filter import filter_upl_violations

from src.config import settings
from src.models.enums import (
    AZ_GOV_NOTICE_DAYS,
    AZ_SOL_YEARS,
    Channel,
    Disposition,
    IntakeField,
    Sentiment,
)
from src.models.intake import IntakeRecord


SYSTEM_PROMPT = """You are an automated assistant for Marigold Injury Law, a personal injury firm in Phoenix, Arizona. You are NOT a lawyer.

YOUR ROLE:
- Collect missing intake information for accident cases
- Be warm, empathetic, and professional
- Adapt your tone to the caller's sentiment
- Never give legal advice, case value estimates, or opinions on merit

HARD RULES (NEVER VIOLATE):
1. You are NOT an attorney. Say so in every opener.
2. NEVER estimate case value, strength, or likelihood of winning.
3. NEVER advise on legal strategy or statute of limitations deadlines.
4. If asked "do I have a good case?" → redirect to fact collection.
5. If someone insists on legal advice → escalate to human.
6. Always disclose this is an automated assistant and calls are recorded.

OPENER (first message of every interaction):
- Voice: "Hi, is this [name]? This is the automated assistant with Marigold Injury Law — I'm not an attorney, and just so you know I record these calls for accuracy."
- SMS/Email: "Hi! This is Marigold's automated assistant (not a lawyer 🙂). I'm following up on your accident — when's a good time to collect some details?"

INTAKE SLOTS TO COLLECT:
- accident_date
- accident_location  
- accident_type (car, motorcycle, pedestrian, slip-and-fall, etc.)
- injuries (describe)
- treatment_status (treating, finished, not treated)
- at_fault_insurance (other driver's insurer, if known)
- prior_attorney_contact (yes/no)

SENTIMENT RESPONSES:
- Positive/neutral: efficient, warm
- Frustrated/hostile: de-escalate, slow down, acknowledge feelings
- In pain/distressed: extra empathy, shorter questions
- If hostile after greeting: "I'm sorry to bother you. Would you like me to stop contacting you?"

WRONG NUMBER: If the person says they're not the lead, immediately stop collecting info. Say "I'm sorry to bother you — I'll make sure this number is removed. Have a good day."

TOOL CALLS:
- update_slot: Record an intake field with value and confidence
- derive_sol_date: Compute statute of limitations (flag for attorney, NEVER spoken)
- send_intake_link: Send link to online intake form
- send_retainer: Send representation agreement for e-signature
- check_retainer_status: Check if retainer was signed
- escalate_to_human: Transfer to a live person
- mark_wrong_number: Suppress this contact
- set_language: Set preferred language
- set_opt_out: Opt the person out of all contact"""


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "update_slot",
            "description": "Record an intake field value with confidence level",
            "parameters": {
                "type": "object",
                "properties": {
                    "field": {
                        "type": "string",
                        "enum": [
                            "accident_date",
                            "accident_location",
                            "accident_type",
                            "injuries",
                            "treatment_status",
                            "at_fault_insurance",
                            "prior_attorney_contact",
                            "name",
                            "dob",
                            "phone",
                            "email",
                        ],
                    },
                    "value": {"description": "The collected value"},
                    "confidence": {
                        "type": "number",
                        "description": "Confidence 0-1 (1=confirmed by caller, 0.5=inferred)",
                    },
                },
                "required": ["field", "value", "confidence"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "derive_sol_date",
            "description": "Derive statute of limitations date from accident date. This is NEVER spoken to the caller — flagged for attorney only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "accident_date": {"type": "string", "format": "date"},
                    "claim_type": {"type": "string"},
                },
                "required": ["accident_date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": "Transfer to a live person. Use when: caller insists on legal advice, sustained hostility, distress, high-value/edge case.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_wrong_number",
            "description": "Mark this contact as wrong number. Stop all collection.",
            "parameters": {
                "type": "object",
                "properties": {
                    "contact_point": {"type": "string"},
                },
                "required": ["contact_point"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_opt_out",
            "description": "Opt person out of all contact channels.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channels": {"type": "string", "enum": ["all", "voice", "sms", "email"]},
                },
                "required": ["channels"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_language",
            "description": "Set the person's preferred language.",
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {"type": "string", "enum": ["en", "es"]},
                },
                "required": ["language"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_retainer",
            "description": "Send representation agreement for e-signature.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "enum": ["sms", "email"]},
                },
                "required": ["channel"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_intake_link",
            "description": "Send link to online intake form.",
            "parameters": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string", "enum": ["sms", "email"]},
                },
                "required": ["channel"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_consent",
            "description": "Record consent for channels.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ai_voice_consent": {"type": "boolean"},
                    "recording_consent": {"type": "boolean"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_retainer_status",
            "description": "Check if the retainer has been signed.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


class ConversationBrain:
    """The conversation agent — stateless, channel-blind, goal-directed."""

    async def run_turn(
        self,
        intake_record: dict,
        channel: str,
        lead_id: str,
        inbound_message: str | None = None,
        message_goal: str | None = None,
        plan_reason: str | None = None,
        constraints: list[str] | None = None,
    ) -> dict:
        """Execute one conversation turn.

        Args:
            intake_record: Current state of the lead
            channel: Which channel this turn is on
            lead_id: Lead identifier
            inbound_message: If this is a response to inbound, the message

        Returns:
            {
                "content": {"text": "...", "type": "voice"|"sms"|"email"},
                "slots": [{"field": "...", "value": "...", "confidence": 0.9}],
                "sentiment": "neutral",
                "action": "continue"|"stop"|"escalate"|"wrong_number",
                "disposition": str | None,
            }
        """
        # Build conversation history from intake state
        history = self._build_history(intake_record, channel)

        # Add inbound message if present
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history)

        if inbound_message:
            messages.append({"role": "user", "content": inbound_message})

        # Channel-specific context injection
        channel_context = self._get_channel_context(channel, intake_record)
        voice_context = intake_record.get("_voice_context")
        if voice_context:
            messages.append({"role": "system", "content": voice_context})
        if channel_context:
            messages.append({"role": "system", "content": channel_context})

        if message_goal:
            strategy_lines = [
                f"Strategy for this touch: {message_goal}",
            ]
            if plan_reason:
                strategy_lines.append(f"Reason: {plan_reason}")
            if constraints:
                strategy_lines.append(f"Constraints: {', '.join(constraints)}")
            strategy_lines.append(
                "Do not re-ask fields already in Collected info unless confirming."
            )
            messages.append({"role": "system", "content": "\n".join(strategy_lines)})

        lang = intake_record.get("engagement", {}).get("language_observed", "en")
        if lang == "es":
            messages.append({"role": "system", "content": "Respond entirely in Spanish."})

        # Call LLM
        llm_response = await chat_completion(
            messages=messages,
            tools=TOOLS,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
        )

        # Process response
        result = {
            "content": None,
            "slots": [],
            "sentiment": "neutral",
            "action": "continue",
            "disposition": None,
        }

        # Extract text content
        if llm_response.content:
            filtered = filter_upl_violations(llm_response.content)
            result["content"] = {
                "text": filtered,
                "type": "voice" if channel == "voice" else "text",
            }

        # Process tool calls
        if llm_response.tool_calls:
            for tool_call in llm_response.tool_calls:
                tool_result = self._process_tool_call(tool_call, intake_record)
                if tool_result:
                    if tool_result.get("type") == "slot":
                        result["slots"].append(tool_result)
                    elif tool_result.get("type") == "action":
                        result["action"] = tool_result["action"]
                    elif tool_result.get("type") == "disposition":
                        result["disposition"] = tool_result["disposition"]

        # Detect sentiment from conversation
        result["sentiment"] = self._detect_sentiment(messages, intake_record)

        return result

    def _build_history(self, intake_record: dict, channel: str) -> list[dict]:
        """Build conversation history from intake state."""
        history = []

        # Add intake context
        identity = intake_record.get("identity", {})
        if identity.get("name"):
            history.append({
                "role": "system",
                "content": f"Lead name: {identity['name']}",
            })

        # Add filled slots as context
        slots = intake_record.get("intake_slots", {})
        if slots:
            slot_summary = "Collected info: " + ", ".join(
                f"{k}: {v.get('value', 'pending')}" for k, v in slots.items()
            )
            history.append({"role": "system", "content": slot_summary})

        # Add engagement state
        engagement = intake_record.get("engagement", {})
        if engagement.get("sentiment"):
            history.append({
                "role": "system",
                "content": f"Current sentiment: {engagement['sentiment']}",
            })

        # Conversation turns from event history
        for turn in intake_record.get("_conversation", []):
            role = turn.get("role")
            content = turn.get("content", "")
            if role in ("user", "assistant") and content:
                history.append({"role": role, "content": content})

        return history

    def _get_channel_context(self, channel: str, intake_record: dict) -> str:
        """Channel-specific context injection."""
        profiles = {
            "voice": "Channel: Voice call. Keep responses to 1-2 sentences. No links. Be conversational.",
            "sms": "Channel: SMS. Keep under 300 chars. Links OK. Be concise.",
            "email": "Channel: Email. Can be longer. Include subject line suggestion. Links OK.",
        }
        return profiles.get(channel, "")

    def _process_tool_call(self, tool_call: Any, intake_record: dict) -> dict | None:
        """Process a single tool call from the LLM."""
        func_name = tool_call.function.name
        try:
            args = __import__("json").loads(tool_call.function.arguments)
        except Exception:
            return None

        if func_name == "update_slot":
            return {
                "type": "slot",
                "field": args.get("field"),
                "value": args.get("value"),
                "confidence": args.get("confidence", 0.8),
            }

        elif func_name == "derive_sol_date":
            accident_date_str = args.get("accident_date")
            if accident_date_str:
                try:
                    accident_date = date.fromisoformat(accident_date_str)
                    sol_date = accident_date + timedelta(days=AZ_SOL_YEARS * 365)
                    gov_notice = accident_date + timedelta(days=AZ_GOV_NOTICE_DAYS)
                    return {
                        "type": "disposition",
                        "disposition": None,
                        "sol_flag": {
                            "sol_date": sol_date.isoformat(),
                            "gov_notice_deadline": gov_notice.isoformat(),
                            "flagged_for_attorney": True,
                            "spoken_to_caller": False,
                        },
                    }
                except (ValueError, OverflowError):
                    pass
            return None

        elif func_name == "escalate_to_human":
            return {"type": "action", "action": "escalate", "reason": args.get("reason")}

        elif func_name == "mark_wrong_number":
            return {"type": "action", "action": "wrong_number"}

        elif func_name == "set_opt_out":
            return {"type": "action", "action": "stop", "disposition": "OPTED_OUT"}

        elif func_name == "set_language":
            return {
                "type": "slot",
                "field": "preferred_lang",
                "value": args.get("language", "en"),
                "confidence": 1.0,
            }

        elif func_name == "send_retainer":
            return {
                "type": "action",
                "action": "send_retainer",
                "channel": args.get("channel", "sms"),
            }

        elif func_name == "send_intake_link":
            return {
                "type": "action",
                "action": "send_intake_link",
                "channel": args.get("channel", "sms"),
            }

        elif func_name == "set_consent":
            return {
                "type": "action",
                "action": "set_consent",
                "ai_voice_consent": args.get("ai_voice_consent", False),
                "recording_consent": args.get("recording_consent", False),
            }

        elif func_name == "check_retainer_status":
            retainer = intake_record.get("retainer", {})
            status = retainer.get("status", "not_sent")
            return {
                "type": "action",
                "action": "continue",
                "retainer_status": status,
            }

        return None

    def _detect_sentiment(self, messages: list[dict], intake_record: dict) -> str:
        """Lightweight sentiment detection from conversation context."""
        # Check if hostile signals in recent messages
        hostile_signals = ["stop calling", "how did you get", "don't call", "hell", "damn", "lawyer"]
        distress_signals = ["hurting", "pain", "scared", "don't know what to do", "hospital"]

        recent_text = " ".join(
            m.get("content", "") for m in messages[-3:] if m.get("role") == "user"
        ).lower()

        for signal in hostile_signals:
            if signal in recent_text:
                return Sentiment.HOSTILE.value

        for signal in distress_signals:
            if signal in recent_text:
                return Sentiment.DISTRESSED.value

        # Check existing state
        engagement = intake_record.get("engagement", {})
        return engagement.get("sentiment", Sentiment.NEUTRAL.value)
