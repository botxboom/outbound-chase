# Outbound-Chase Agent — Solution Presentation

**Project:** AI intake chase agent for Marigold Injury Law (personal injury)  
**Author:** Gulshan Kumar  
**Repo:** `outbound-chase`

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement](#2-problem-statement)
3. [Design Philosophy](#3-design-philosophy)
4. [Architecture Overview](#4-architecture-overview)
5. [Data Model & CQRS](#5-data-model--cqrs)
6. [Adaptive Strategy Layer](#6-adaptive-strategy-layer)
7. [Conversation Brain](#7-conversation-brain)
8. [Temporal Orchestrator](#8-temporal-orchestrator)
9. [Channel Adapters](#9-channel-adapters)
10. [Ingress Matrix (Channel-Agnostic)](#10-ingress-matrix-channel-agnostic)
11. [API & Entry Points](#11-api--entry-points)
12. [Six Caller Scenarios](#12-six-caller-scenarios)
13. [Tech Stack & Tools Used](#13-tech-stack--tools-used)
14. [Stubs — What & Why](#14-stubs--what--why)
15. [How to Run (Demo Script)](#15-how-to-run-demo-script)
16. [Testing](#16-testing)
17. [How I Worked With AI](#17-how-i-worked-with-ai)
18. [Gaps & Two-Week Roadmap](#18-gaps--two-week-roadmap)
19. [Repository Map](#19-repository-map)
20. [One-Slide Pitch](#20-one-slide-pitch)

---

## 1. Executive Summary

This is a **working slice** of an outbound intake chase system for a personal injury law firm. It demonstrates:

- **Channel-agnostic intake state** — same engine for missed calls, web forms, SMS replies, and (designed) web chat
- **Adaptive strategy** — reads signals (SMS reply, "text me", hostile tone) and changes *what to do next*, not just what to say
- **Durable orchestration** — 14-day ghosting cadence with timer-vs-inbound race via Temporal
- **LLM conversation brain** — slot filling, UPL guardrails, sentiment handling, Spanish parity
- **Six caller scenarios** — responsive, ghosting, hostile, wrong number, legal advice, Spanish-only

**Honest positioning:** Architecture and core adaptive behaviors are solid (~65–70% of the ideal product). It does not yet fully meet the "best human intake specialist" bar — strategy is **rules-first with optional LLM signal extraction**, not deep psychological modeling. That gap is documented with a clear roadmap.

---

## 2. Problem Statement

Law firms receive **partial leads** — someone started intake (web form, missed call, chat) but dropped off. A great human intake specialist:

- Reads who they're talking to
- Adapts channel and timing in the moment ("they texted back — stop calling")
- Handles situations no script anticipated (hostile, legal advice requests, wrong number, Spanish-only)
- Chases persistently but compliantly over 14 days without giving up after one voicemail

The bar is **not a script**. It's a system that genuinely understands context and responds the way a thoughtful human would.

---

## 3. Design Philosophy

### Why this decomposition?

| Layer | Responsibility | Why separate? |
|-------|----------------|---------------|
| **Orchestrator** (Temporal) | When to act, durability, compliance gates, wait for inbound | Legal chase spans days; must survive restarts |
| **Strategy** | Who this person is, next channel/timing/goal | Humans adapt the *plan*, not just the script |
| **Brain** (LLM) | What to say on this turn | Language is hard; policy is auditable elsewhere |
| **Channels** | How to send (voice/SMS/email) | Swap Vapi/Twilio/mock without touching logic |

### Core invariants

1. **Planner proposes, gates dispose** — compliance (opt-out, quiet hours, attempt caps) always vetoes the plan
2. **Event log is source of truth** — CQRS projection rebuilds `IntakeRecord` from append-only events
3. **Channel-blind brain** — LLM emits intent + content; adapters handle delivery
4. **Cadence is fallback, not driver** — fixed 9-step schedule only when no engagement signals exist

### What I deliberately did NOT build

- Web chat UI (brief said not to — only ingress design)
- LLM-driven compliance decisions (too risky for legal)
- Pure LLM planner for channel selection (not auditable)

---

## 4. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        LEAD INGRESS                              │
│  POST /api/leads │ Clio Grow fixture │ simulate/inbound │ Vapi  │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                   TEMPORAL WORKFLOW                              │
│         OutboundChaseWorkflow (one per lead)                     │
│   plan → gate → execute → wait OR inbound → re-plan              │
└───────┬─────────────────────────────────────────┬───────────────┘
        │                                         │
        ▼                                         ▼
┌───────────────────┐                   ┌─────────────────────────┐
│  STRATEGY LAYER   │                   │   CONVERSATION BRAIN    │
│  extract_signals  │                   │   LLM + tools + UPL     │
│  plan_next_touch  │── message_goal ──▶│   channel-blind         │
│  cadence fallback │                   └───────────┬─────────────┘
└─────────┬─────────┘                               │
          │                                         ▼
          │                               ┌─────────────────────────┐
          │                               │   CHANNEL ADAPTERS      │
          └──────────────────────────────▶│ voice / sms / email     │
                                            └─────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                     CQRS STATE (PostgreSQL)                      │
│   events (append-only) ──▶ leads projection (IntakeRecord)      │
└─────────────────────────────────────────────────────────────────┘
```

### Request flow (single outbound touch)

1. Workflow calls `plan_next_touch(lead_id)`
2. Strategy reads engagement model → returns channel, message_goal, wait_seconds, reason
3. `check_compliance_gates` vetoes if opt-out / quiet hours / caps
4. `execute_outbound_touch` → brain generates content → adapter sends
5. `extract_signals` updates person model from interaction
6. Workflow sleeps or races against inbound signal
7. On inbound → `process_inbound` → brain reply → re-plan from scratch

---

## 5. Data Model & CQRS

### IntakeRecord (channel-agnostic)

Single projection used by orchestrator, strategy, and brain:

| Area | Key fields |
|------|------------|
| **Identity** | name, phone[], email[] |
| **Consent** | origin (inbound/outbound), channels_allowed, ai_voice_consent, opt_out |
| **Intake slots** | accident_date, location, type, injuries, treatment, insurance, prior_attorney |
| **Engagement** | sentiment, attempts_by_channel, channel_preference, responsiveness, do_not_call, engagement_mode, contact_window, last_plan, cadence_step_index, trigger |
| **Disposition** | NEW → CHASING → ENGAGED → INTAKE_COMPLETE → RETAINER_SENT → SIGNED |
| **Terminal** | OPTED_OUT, HANDOFF, GAVE_UP, WRONG_NUMBER |
| **Retainer** | envelope_id, status, signed_pdf_ref |

### Event-sourced projection

- **`events` table** — append-only log
- **`leads` table** — materialized projection for fast reads
- **`apply_event()`** in `src/store/projection.py` — reducer pattern

**Key event types:**

- Lifecycle: `lead.created`, `form.submitted`
- Channels: `sms.inbound`, `sms.outbound`, `call.attempted`, `call.voicemail_left`, `email.sent`
- Intake: `slot.captured`, `slot.confirmed`
- Strategy: `strategy.signal_observed`, `strategy.planned`
- Terminal: `consent.revoked`, `escalated`, `gave_up`, `retainer.signed`

**Why CQRS?** Audit trail for compliance, cross-channel conversation history, ability to rebuild state.

---

## 6. Adaptive Strategy Layer

**Files:** `src/orchestrator/strategy.py`, `src/orchestrator/signal_extractor.py`

### Closed loop

```
observe → extract signals → update person model → plan next touch → brain executes → repeat
```

### Signal extraction

**Rules (always on):**

| Signal | Effect |
|--------|--------|
| SMS inbound | `responsiveness[sms]=hot`, prefer SMS |
| Reply within 5 min | `hot` on that channel |
| "Text me" / "stop calling" | `do_not_call=true`, SMS only |
| Hostile keywords | `engagement_mode=hostile_open`, de-escalation path |
| 3+ outbound, no inbound | `engagement_mode=ghosting` → cadence fallback |
| "Call after 5" / "evening" | `contact_window` set |

**Optional LLM** (`STRATEGY_LLM_SIGNALS=true`, 5s timeout):

- Runs for inbound messages > 20 chars
- Returns JSON: channel_preference, do_not_call, contact_window, objections
- **Rules win** on compliance-critical fields (opt-out, do_not_call)

### Planner rule priority

1. Terminal (14-day timeout, max attempts) → give up
2. `do_not_call` or SMS-hot → **never voice**
3. Hostile, not opted out → empathy SMS, 48h wait
4. Intake complete → send retainer
5. Engaged + partial slots → gap_fill on best channel
6. **Fallback:** 9-step cadence curve

Every plan emits `strategy.planned` event with auditable `reason` string (e.g. `adaptive:sms_hot_responsive`, `cadence_fallback:step_1_call_attempt`).

### Message goals (passed to brain)

| Goal | When used |
|------|-----------|
| `instant_reply` | Missed call / inbound origin |
| `warm_intro` | Empty web form |
| `confirm_slots` | Partial web form |
| `gap_fill` | Engaged, missing slots |
| `empathy_check_in` | Hostile but not opted out |
| `soft_reengage` | Clio Grow stale lead |
| `call_attempt` | Cadence fallback voice |
| `loss_aversion` | Day 7 ghosting |
| `send_retainer` | Intake complete |
| `give_up` | 14-day timeout |

---

## 7. Conversation Brain

**File:** `src/brain/agent.py`

### Role

Automated assistant for Marigold Injury Law. **Not a lawyer.** Collects intake slots, adapts tone, never gives legal advice.

### LLM tools (function calling)

| Tool | Purpose |
|------|---------|
| `update_slot` | Capture intake field with confidence |
| `derive_sol_date` | AZ SOL flag for attorney only — never spoken |
| `send_intake_link` | Online intake form link |
| `send_retainer` | eSign representation agreement |
| `check_retainer_status` | Poll signing status |
| `escalate_to_human` | Transfer to live person |
| `mark_wrong_number` | Suppress contact |
| `set_opt_out` | Stop all contact |
| `set_language` | Set preferred language |

### Guardrails

1. **Prompt-level UPL rules** — no case value, no merit opinions, redirect legal questions
2. **Deterministic UPL filter** (`src/brain/upl_filter.py`) — regex backstop on outbound text
3. **Sentiment detection** — keyword-based hostile/distressed signals
4. **Opener disclosure** — automated assistant, not attorney, calls recorded

### Strategy → brain injection

When executing a planned touch, brain receives:

```
Strategy for this touch: gap_fill
Reason: adaptive:sms_hot_responsive
Constraints: under 300 chars, no voice mention
Do not re-ask fields already in Collected info unless confirming.
```

---

## 8. Temporal Orchestrator

**Files:** `src/orchestrator/workflow.py`, `src/orchestrator/activities.py`, `src/worker.py`

### Workflow ID

`chase-{lead_id}` on task queue `outbound-chase`

### Signals

| Signal | Effect |
|--------|--------|
| `inbound_event` | SMS/email/voice reply arrives — interrupts timer |
| `stop_chase` | External stop |
| `opt_out` | Immediate opt-out |

### Compliance gates (hard veto)

| Gate | Behavior |
|------|----------|
| Opt-out | Terminal OPTED_OUT |
| Quiet hours | 8 AM – 9 PM Phoenix — wait/re-plan |
| Voice cap | Max 3 voice attempts |
| SMS cap | Max 5 SMS attempts |
| Email cap | Max 3 email attempts |
| Total cap | Max 10 attempts across channels |
| Chase timeout | 14 days → GAVE_UP |
| AI voice consent | Required unless `DEMO_AI_VOICE_CONSENT=true` |

### Cadence fallback table

Used when no strong engagement signals (ghosting):

| Step | Channel | Goal | Backoff |
|------|---------|------|---------|
| 0 | SMS | instant_reply | 0 |
| 1 | Voice | call_attempt | 5 min |
| 2 | SMS | follow_up | 1 hr |
| 3 | Voice | call_attempt (alt hour) | 1 day |
| 4 | Email | intake_link | 2 days |
| 5 | Voice | call_attempt | 4 days |
| 6 | SMS | loss_aversion | 7 days |
| 7 | Email | final | 10 days |
| 8 | — | give_up | 14 days |

**Voice AMD=machine** → unique voicemail + paired SMS (attempt-aware wording).

---

## 9. Channel Adapters

**Pattern:** `src/channels/base.py` → mock / real implementations selected by config.

| Channel | Mock (default) | Production | Config |
|---------|----------------|------------|--------|
| Voice | `SimulatedVoiceAdapter` | Vapi PSTN | `VOICE_MODE=simulated\|vapi` |
| SMS | `MockSMSAdapter` | Twilio | `USE_MOCK_CHANNELS=false` + Twilio keys |
| Email | `MockEmailAdapter` | Postmark | Postmark token |
| eSign | Mock envelope IDs | Dropbox Sign | `DROPBOX_SIGN_API_KEY` |
| Clio | Audit log only | Clio Manage API | `CLIO_ACCESS_TOKEN` |

Mock adapters write to `audit_trail` table for demo visibility.

---

## 10. Ingress Matrix (Channel-Agnostic)

Same engine, same workflow, same brain — different first plan based on ingress:

| Source | API payload | First plan | Notes |
|--------|-------------|------------|-------|
| **Missed call** | `consent.origin: "inbound"` | SMS `instant_reply` | Acknowledge missed call |
| **Web form (partial)** | `trigger: "web_form"` + `intake_slots` | SMS `confirm_slots` | Confirm known, ask one gap |
| **Web form (empty)** | `trigger: "web_form"` | SMS `warm_intro` | Standard opener |
| **Clio Grow** | `trigger: "clio_grow_stub"` via fixture poll | SMS `soft_reengage` | 24h wait before voice |
| **Web chat** | `trigger: "web_chat"` + optional conversation seed | SMS `gap_fill` | No UI built; same API |

**Example — missed call:**

```json
POST /api/leads
{
  "identity": {"name": "David", "phone": ["+14805551234"], "email": ["david@example.com"]},
  "consent": {"origin": "inbound", "channels_allowed": ["voice","sms","email"], "ai_voice_consent": true}
}
```

**Example — partial web form:**

```json
POST /api/leads
{
  "trigger": "web_form",
  "identity": {"name": "Maria", "phone": ["+14805559999"]},
  "intake_slots": {
    "accident_date": {"value": "2026-03-01", "confidence": 0.7}
  }
}
```

No rework per channel — only ingress payload differs.

---

## 11. API & Entry Points

**Server:** FastAPI on port 8000 (`src/api/webhooks.py`)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/leads` | POST | Create lead, start Temporal workflow |
| `/api/leads/{id}` | GET | Full intake projection + engagement |
| `/api/leads/{id}/simulate/inbound` | POST | Inject SMS/email without Twilio |
| `/api/leads/{id}/simulate/voice` | POST | Simulate voice call + AMD result |
| `/api/leads/{id}/simulate/esign` | POST | Simulate retainer signed |
| Vapi custom-LLM route | POST | Live voice turns → brain |

**Worker:** `python -m src.main --mode worker`

**Scenario CLI:** `scripts/run_scenario.py` — demos without Temporal UI

**Clio Grow poll:** `python -m src.integrations.clio_grow --poll`

---

## 12. Six Caller Scenarios

| Scenario | What it proves | CLI command | Test file |
|----------|----------------|-------------|-----------|
| **Responsive** | SMS happy path, slot capture, adaptive SMS plan | `python3 scripts/run_scenario.py responsive --channel sms` | `test_async_scenarios.py` |
| **Ghosting** | 14-day cadence fallback, voicemail + paired SMS, `[PLAN]` output | `python3 scripts/run_scenario.py ghosting` | `test_workflow_ghosting.py` |
| **Hostile** | De-escalation, opt-out, escalate_to_human | `python3 scripts/run_scenario.py hostile --channel voice` | `test_voice_scenarios.py` |
| **Wrong number** | Immediate suppression, no slot collection | `python3 scripts/run_scenario.py wrong_number --channel voice` | `test_voice_scenarios.py` |
| **Legal advice** | UPL redirect, no merit/case value opinions | `python3 scripts/run_scenario.py legal_advice --channel voice` | `test_voice_scenarios.py` |
| **Spanish** | Language detection, Spanish responses | `python3 scripts/run_scenario.py spanish --channel voice` | `test_async_scenarios.py` |

**Caller scripts:** `tests/sim/caller_scripts.py` (design doc §7 utterances)

**Voice driver:** `tests/sim/voice_driver.py` — multi-turn simulated voice with real LLM, no PSTN

---

## 13. Tech Stack & Tools Used

### Runtime & infrastructure

| Tool | Version / notes | Role |
|------|-----------------|------|
| Python | 3.11+ | Application language |
| Temporal | 1.24 | Durable workflow orchestration |
| Temporal UI | 2.21 | Workflow monitoring (localhost:8081) |
| PostgreSQL | 16 | Events + lead projection |
| Docker Compose | — | Postgres, Temporal, app, worker |
| Alembic | — | DB migrations |
| FastAPI + Uvicorn | — | HTTP API |
| SQLAlchemy (async) + asyncpg | — | ORM / DB driver |
| Pydantic v2 | — | Models + settings |

### AI / LLM

| Tool | Role |
|------|------|
| OpenAI-compatible API (`openai` SDK) | Brain + optional strategy signal extraction |
| Ollama / remote qwen3:8b | Dev LLM (remote endpoint via Tailscale in my setup) |
| Function calling / tools | Slot capture, escalate, opt-out, retainer |
| `think: false` for qwen3 | Faster replies on tool-calling turns |

### Channels (integrated, often stubbed)

| Tool | Role |
|------|------|
| Vapi | Live voice — custom-LLM URL points to this server |
| Twilio | SMS adapter ready |
| Postmark | Email adapter ready |
| Dropbox Sign | eSign retainer |
| Clio Manage | Case milestone write-back |

### Dev & quality

| Tool | Role |
|------|------|
| pytest + pytest-asyncio | 44+ unit, 8+ integration tests |
| ruff | Linting |
| mypy | Type checking (configured) |
| structlog | Structured logging |

### Development environment

| Tool | Role |
|------|------|
| **Cursor IDE** | Primary development environment |
| **Cursor Agent (AI)** | Architecture, implementation, debugging, strategy layer |
| Docker | Local Postgres (port 5434) + Temporal |
| Temporal UI | Workflow progress monitoring |

---

## 14. Stubs — What & Why

| Component | What was stubbed | Why | Production path |
|-----------|------------------|-----|-----------------|
| **Clio Grow** | `fixtures/partial_leads.json` + poll script | No sandbox signup completed | Real Grow API poll → same `create_lead` |
| **Clio Manage** | Audit log, `{mock: true}` response | No API token | Set `CLIO_ACCESS_TOKEN` |
| **SMS** | `MockSMSAdapter` — in-memory + audit | No Twilio creds for demo/CI | `USE_MOCK_CHANNELS=false` + Twilio keys |
| **Email** | `MockEmailAdapter` | No Postmark token | Postmark token in `.env` |
| **Voice PSTN** | `SimulatedVoiceAdapter` | Developer in India; US number on Vapi only | `VOICE_MODE=vapi` + Vapi keys |
| **eSign** | `mock-env-{lead_id}` envelopes | No Dropbox Sign key | `DROPBOX_SIGN_API_KEY` |
| **AMD** | Configurable per lead in mock | Test voicemail path without real calls | Vapi AMD webhook |

**Blocker workaround (voice):** Built `SimulatedVoiceAdapter` + `voice_driver.py` so all six scenarios run with real LLM but zero PSTN — same brain endpoint powers simulated and live voice.

---

## 15. How to Run (Demo Script)

### Prerequisites

```bash
pip install -e ".[dev]"
cp .env.example .env
# Edit .env: DATABASE_URL (port 5434), LLM_BASE_URL, LLM_MODEL
```

### Infrastructure

```bash
docker compose up -d postgres temporal temporal-ui
# Postgres: localhost:5434
# Temporal UI: http://localhost:8081
```

### Full stack

```bash
# Terminal 1
python -m src.main --mode worker

# Terminal 2
python -m src.main --mode server
```

### Scenario demos (no Temporal required)

```bash
python3 scripts/run_scenario.py responsive --channel sms
python3 scripts/run_scenario.py ghosting
python3 scripts/run_scenario.py hostile --channel voice
python3 scripts/run_scenario.py legal_advice --channel voice
python3 scripts/run_scenario.py wrong_number --channel voice
python3 scripts/run_scenario.py spanish --channel voice
```

### API demo

```bash
# Create lead (starts workflow)
curl -X POST http://localhost:8000/api/leads \
  -H 'Content-Type: application/json' \
  -d '{
    "identity": {"name": "David", "phone": ["+14805551234"], "email": ["david@example.com"]},
    "consent": {"origin": "inbound", "channels_allowed": ["voice","sms","email"], "ai_voice_consent": true}
  }'

# Check progress
curl http://localhost:8000/api/leads/{lead_id}

# Simulate SMS reply (without Twilio)
curl -X POST http://localhost:8000/api/leads/{lead_id}/simulate/inbound \
  -H 'Content-Type: application/json' \
  -d '{"channel": "sms", "message": "Hi sorry I missed your call, yes I was in an accident"}'
```

### Monitor workflow

- **Temporal UI:** http://localhost:8081 → workflow `chase-{lead_id}`
- **Lead state:** `GET /api/leads/{lead_id}` → disposition, engagement.responsiveness, last_plan

---

## 16. Testing

```bash
# Unit tests (no LLM, fast)
pytest tests/test_strategy.py tests/test_scenarios.py tests/test_projection.py -v

# Integration (uses DB)
pytest tests/integration/ -v

# LLM integration (requires LLM endpoint)
pytest tests/integration/ -m llm -v
```

### Key test coverage

| Test file | Proves |
|-----------|--------|
| `test_strategy.py` | SMS-hot → no voice, hostile de-escalation, ingress plans |
| `test_strategy_adaptive.py` | DB-persisted signals, inbound → SMS plan |
| `test_workflow_ghosting.py` | Voicemail + paired SMS via plan fallback |
| `test_scenarios.py` | UPL, compliance gates, disposition transitions |
| `test_voice_scenarios.py` | Voice + LLM scenarios (@pytest.mark.llm) |
| `test_async_scenarios.py` | SMS + Spanish (@pytest.mark.llm) |

---

## 17. How I Worked With AI

**Tool:** Cursor IDE with Agent mode (AI coding assistant).

### Division of work

| I decided / verified | AI implemented / drafted |
|----------------------|---------------------------|
| Problem framing ("human specialist, not script") | Temporal workflow boilerplate |
| Architecture split (orchestrator / strategy / brain / channels) | CQRS projection, API routes |
| Identified strategy gap (fixed cadence vs adaptive plan) | Strategy layer per spec |
| Which scenarios to demo | Test scaffolding, README |
| LLM output quality on hostile/UPL scenarios | Debugging infra issues |
| Honest gap analysis for submission | Initial cadence-first approach (corrected) |

### Where AI was wrong — and how I corrected it

| Issue | AI mistake | My correction |
|-------|------------|---------------|
| Adaptiveness | Built fixed 9-step cadence as primary driver | Added strategy layer: observe → plan → act |
| Data types | Assigned `"neutral"` string to `Sentiment` enum field | Coerce to enum on assignment |
| Postgres | Default port 5432 conflicted with Homebrew Postgres | Docker mapped to host port 5434 |
| Temporal | Workflow sandbox blocked pathlib in activities | `UnsandboxedWorkflowRunner` + lazy imports |
| Datetime | Naive vs aware datetime in compliance gates | Normalize timezone in gate check |
| LLM latency | No progress indication; looked "stuck" | Added turn logging, `think: false`, 180s timeout |

### What I verified manually

- Hostile and legal-advice voice outputs (not just unit tests)
- SMS adaptive plan after inbound (integration test + CLI)
- Ghosting still produces voicemails via cadence fallback (not SMS-hot shortcut)
- Remote qwen3 ~60s/turn latency acceptable for demo

### Trace

Development conducted in Cursor Agent sessions. Link your Cursor chat export or agent transcript in submission if required.

---

## 18. Gaps & Two-Week Roadmap

### Where it still falls short of "best human intake specialist"

1. **Strategy is rules + keywords** — not deep subtext ("sounds hesitant but interested")
2. **Conversation memory thin** — voice turns may not fully carry across channels
3. **No eval harness** — can't score "feels human" at scale
4. **Voice CLI bypasses Temporal** — activities called directly in scenario script
5. **`contact_window` extracted but not enforced** in quiet-hours scheduling
6. **Email-opened signal unused** in planner
7. **Clio sandbox not integrated** — fixture only

### Next two weeks

| Priority | Work |
|----------|------|
| 1 | Persona eval harness — 20 synthetic leads, expected plan assertions |
| 2 | Temporal E2E for all six scenarios with time-skip |
| 3 | Clio sandbox — real Grow ingest + Manage write-back |
| 4 | Contact window enforcement — "call after 5pm" actually deferred |
| 5 | One live channel proof — Twilio SMS or Vapi voice |
| 6 | LLM planner for ambiguous multi-signal cases (with audit log) |

---

## 19. Repository Map

```
outbound-chase/
├── PRESENTATION.md              ← this document
├── README.md                    ← runbook
├── pyproject.toml               ← dependencies
├── docker-compose.yml           ← postgres, temporal, app, worker
├── Dockerfile
├── alembic/                     ← DB migrations
├── fixtures/
│   └── partial_leads.json       ← Clio Grow stub
├── scripts/
│   └── run_scenario.py          ← demo CLI
├── src/
│   ├── main.py                  ← server | worker entry
│   ├── config.py                ← settings
│   ├── worker.py                ← Temporal worker
│   ├── api/
│   │   ├── webhooks.py          ← FastAPI app
│   │   ├── leads.py             ← lead CRUD + simulate
│   │   └── vapi_llm.py          ← Vapi custom-LLM
│   ├── brain/
│   │   ├── agent.py             ← ConversationBrain
│   │   └── upl_filter.py        ← UPL regex backstop
│   ├── channels/
│   │   ├── base.py              ← adapter interface
│   │   ├── mock.py              ← mock SMS/email/voice
│   │   ├── voice_simulated.py   ← sim voice driver
│   │   ├── voice.py             ← Vapi adapter
│   │   ├── sms.py               ← Twilio
│   │   └── email.py             ← Postmark
│   ├── orchestrator/
│   │   ├── workflow.py          ← Temporal workflow
│   │   ├── activities.py        ← Temporal activities
│   │   ├── strategy.py          ← adaptive planner
│   │   ├── signal_extractor.py  ← hybrid signal extraction
│   │   └── cadence.py           ← fallback cadence table
│   ├── store/
│   │   └── projection.py        ← CQRS event log + projection
│   ├── integrations/
│   │   ├── clio_grow.py         ← Grow stub
│   │   ├── clio.py              ← Manage write-back
│   │   └── dbox_sign.py         ← eSign
│   ├── models/
│   │   ├── intake.py            ← IntakeRecord, EngagementState
│   │   ├── enums.py             ← Disposition, Channel, MessageGoal
│   │   └── events.py            ← Event model
│   └── db/
│       └── models.py            ← SQLAlchemy tables
└── tests/
    ├── test_strategy.py
    ├── test_scenarios.py
    ├── test_projection.py
    ├── integration/
    │   ├── test_strategy_adaptive.py
    │   ├── test_workflow_ghosting.py
    │   ├── test_async_scenarios.py
    │   └── test_voice_scenarios.py
    └── sim/
        ├── caller_scripts.py    ← scenario utterances
        └── voice_driver.py      ← simulated voice harness
```

---

## 20. One-Slide Pitch

> **Outbound-Chase** is a durable, channel-agnostic intake chase engine for PI law firms. Temporal handles the 14-day chase; a **strategy layer** reads engagement signals and adapts channel, timing, and message goals; an LLM brain handles conversation with UPL guardrails. Six caller scenarios run today via CLI and automated tests. Integrations are stubbed where sandbox access wasn't available (Clio, PSTN from India), with clear production paths wired in. The design prioritizes **auditability and adaptiveness over a fixed script** — and documents honestly where human-level judgment still lives on the roadmap.

---

*End of presentation document.*
