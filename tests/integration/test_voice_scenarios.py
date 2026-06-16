"""Voice scenario integration tests — real LLM, simulated voice."""
from __future__ import annotations

import pytest

from tests.sim.caller_scripts import HOSTILE, LEGAL_ADVICE, SPANISH_SMS, WRONG_NUMBER
from tests.sim.voice_driver import run_voice_conversation


@pytest.mark.llm
@pytest.mark.integration
@pytest.mark.asyncio
async def test_legal_advice_refuses_merit(base_intake, require_llm):
    transcript = await run_voice_conversation(base_intake, LEGAL_ADVICE)
    agent_text = " ".join(transcript.agent_texts()).lower()

    assert any(
        phrase in agent_text
        for phrase in ["attorney", "lawyer", "assess", "evaluate", "can't", "cannot"]
    )
    assert "update_slot" in str(transcript.all_tool_fields()) or "accident" in agent_text
    assert "$" not in agent_text or "value" not in agent_text


@pytest.mark.llm
@pytest.mark.integration
@pytest.mark.asyncio
async def test_hostile_opt_out(base_intake, require_llm):
    transcript = await run_voice_conversation(base_intake, HOSTILE)
    last = transcript.turns[-1]

    assert last.brain_result is not None
    assert last.brain_result.get("action") == "stop" or "stop" in last.content.lower()


@pytest.mark.llm
@pytest.mark.integration
@pytest.mark.asyncio
async def test_wrong_number(base_intake, require_llm):
    base_intake["identity"]["name"] = "Maria Lopez"
    transcript = await run_voice_conversation(base_intake, WRONG_NUMBER)
    last = transcript.turns[-1]

    assert last.brain_result is not None
    assert last.brain_result.get("action") == "wrong_number" or "wrong" in last.content.lower()
    assert "accident_date" not in transcript.all_tool_fields()


@pytest.mark.llm
@pytest.mark.integration
@pytest.mark.asyncio
async def test_spanish_parity(base_intake, require_llm):
    base_intake["identity"]["name"] = "Maria"
    transcript = await run_voice_conversation(base_intake, SPANISH_SMS)
    agent_text = " ".join(transcript.agent_texts())

    spanish_markers = ["hola", "accidente", "abogado", "gracias", "sí", "lo siento"]
    assert any(m in agent_text.lower() for m in spanish_markers)
