# Outbound-Chase Agent

AI intake chase agent for Marigold Injury Law — re-engages partial leads across voice, SMS, and email, completes intake, sends retainer for eSign, and writes back to Clio Manage.

## Architecture

Four layers:

1. **Durable orchestrator** (Temporal) — plan-driven loop, compliance gates, timer-vs-inbound race
2. **Adaptive strategy** — signal extraction + touch planner (rules + optional LLM); fixed cadence is fallback only
3. **Conversation brain** (LLM) — channel-blind slot filling, message goals, UPL guardrails
4. **Channel adapters** — voice (simulated/Vapi), SMS (Twilio/mock), email (Resend/mock), eSign (DocuSeal/mock)

State is event-sourced (CQRS): append-only `events` table + `IntakeRecord` projection.

### Strategy layer

After each touch, the system extracts signals (SMS reply → hot, "text me" → no voice) and plans the next move:

- **Channel** — e.g. stop calling after SMS reply
- **Message goal** — gap_fill, confirm_slots, empathy_check_in, etc.
- **Timing** — wait_seconds before next touch

CLI ghosting scenario prints `[PLAN] channel=... goal=... reason=...`.

### Ingress matrix (same engine, different first plan)

| Source | How to ingest | First plan |
|--------|---------------|------------|
| Missed call | `consent.origin=inbound` on POST /api/leads | SMS instant_reply |
| Web form (partial) | `trigger=web_form` + intake_slots | SMS confirm_slots |
| Web form (empty) | `trigger=web_form` | SMS warm_intro |
| Clio Grow stub | `trigger=clio_grow_stub` | SMS soft_reengage |
| Web chat (no UI) | `trigger=web_chat` + optional conversation seed | SMS gap_fill |

## Quick Start

```bash
# Dependencies
pip install -e ".[dev]"

# Start infrastructure
docker compose up -d postgres temporal

# Configure LLM (Ollama example)
export LLM_BASE_URL=http://localhost:11434/v1
export LLM_MODEL=mimo-v2.5
export USE_MOCK_CHANNELS=true
export VOICE_MODE=simulated

# Run worker + server (two terminals)
python -m src.main --mode worker
python -m src.main --mode server

# Create a lead
curl -X POST http://localhost:8000/api/leads \
  -H 'Content-Type: application/json' \
  -d '{
    "identity": {"name": "David", "phone": ["+14805551234"], "email": ["david@example.com"]},
    "consent": {"origin": "inbound", "channels_allowed": ["voice","sms","email"], "ai_voice_consent": true}
  }'
```

Or use Docker Compose for full stack including worker:

```bash
docker compose up --build
```

## Testing Without Vapi (India / non-US numbers)

Voice is tested via **SimulatedVoiceAdapter** — no PSTN calls. The same brain endpoint powers simulated and live voice.

### Run scenario CLI

```bash
# Voice + real LLM
python scripts/run_scenario.py legal_advice --channel voice
python scripts/run_scenario.py hostile --channel voice
python scripts/run_scenario.py spanish --channel voice

# SMS path
python scripts/run_scenario.py responsive --channel sms

# Ghosting cadence (orchestrator only)
python scripts/run_scenario.py ghosting
```

Output uses design-doc annotations: `[PLAN]`, `[GATE]`, `[TOOL]`, `[STATE]`, `[EVENT]`.

### Run pytest

```bash
# Unit tests (no LLM)
pytest tests/test_scenarios.py tests/test_projection.py -v

# Integration + real LLM (requires Ollama/OpenAI at LLM_BASE_URL)
pytest tests/integration/ -m llm -v
```

LLM tests skip automatically if the endpoint is unreachable.

### Simulate inbound without Twilio

```bash
curl -X POST http://localhost:8000/api/leads/{lead_id}/simulate/inbound \
  -H 'Content-Type: application/json' \
  -d '{"channel": "sms", "message": "Hi, I was in an accident last week"}'
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `USE_MOCK_CHANNELS` | `true` | Mock SMS/email/voice/eSign/Clio |
| `VOICE_MODE` | `simulated` | `simulated` or `vapi` |
| `DEMO_AI_VOICE_CONSENT` | `true` | Bypass ai_voice_consent gate for demo |
| `LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible LLM |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | Vapi custom-LLM serverUrl |
| `RESEND_API_KEY` | — | Resend email API key |
| `DOCUSEAL_API_URL` | `http://localhost:3000` | Self-hosted DocuSeal base URL |
| `DOCUSEAL_API_KEY` | — | DocuSeal X-Auth-Token |
| `DOCUSEAL_TEMPLATE_ID_EN` | `0` | Retainer template ID (English) |
| `CLIO_GROW_FIXTURE_PATH` | `fixtures/partial_leads.json` | Clio Grow stub fixture |
| `STRATEGY_LLM_SIGNALS` | `true` | LLM signal extraction for ambiguous inbound |
| `STRATEGY_LLM_TIMEOUT_SECONDS` | `5.0` | Timeout for strategy LLM calls |

## Production vs Demo

- **`DEMO_AI_VOICE_CONSENT=true`** — voice channel allowed without explicit consent (demo only)
- **`VOICE_MODE=simulated`** — no PSTN; set `vapi` + credentials for live calls
- **`USE_MOCK_CHANNELS=false`** — use real Twilio/Resend/DocuSeal/Clio when API keys are set

## DocuSeal (self-hosted eSign)

```bash
docker compose up -d docuseal docuseal-postgres
```

1. Open **http://localhost:3000** — create admin account
2. Create a retainer template → note **Template ID** (URL or template settings)
3. Console → API → copy **API key** (requires DocuSeal Pro license for API on self-hosted)
4. Console → Webhooks → add `http://localhost:8000/webhooks/esign` → event `submission.completed`
5. Set in `.env`:

```bash
USE_MOCK_CHANNELS=false
DOCUSEAL_API_URL=http://localhost:3000
DOCUSEAL_API_KEY=your_api_key
DOCUSEAL_TEMPLATE_ID_EN=1
DOCUSEAL_SUBMITTER_ROLE=First Party
```

## Resend (email)

1. Sign up at [resend.com](https://resend.com) — verify domain or use `onboarding@resend.dev` for testing
2. Set `RESEND_API_KEY` and `EMAIL_FROM_EMAIL` in `.env`
3. Set `USE_MOCK_CHANNELS=false` to send real email

## Clio Grow Stub

Clio Grow read API is stubbed per design §9:

```bash
python -m src.integrations.clio_grow --poll
```

Reads unprocessed leads from `fixtures/partial_leads.json`.

## Six Caller Scenarios (Design §7)

| Scenario | CLI | Test file |
|----------|-----|-----------|
| Legal advice / UPL | `legal_advice` | `test_voice_scenarios.py` |
| Hostile / opt-out | `hostile` | `test_voice_scenarios.py` |
| Responsive happy path | `responsive --channel sms` | `test_async_scenarios.py` |
| Ghosting 14-day | `ghosting` | `test_workflow_ghosting.py` |
| Wrong number | `wrong_number` | `test_voice_scenarios.py` |
| Spanish parity | `spanish` | `test_voice_scenarios.py` + `test_async_scenarios.py` |
