# Outbound-Chase Agent

---

## Summary

I built **Outbound-Chase** — a working slice of an AI intake chase system for a personal injury firm. It re-engages partial leads across SMS, voice, and email over up to 14 days, completes intake, sends a retainer for eSign, and writes back to Clio Manage. The goal was never a fixed script: I wanted something that behaves like a sharp human intake specialist — reads context, adapts channel and timing in the moment, and handles situations no playbook covers.

**What I built.** A four-layer system: Temporal orchestrator (durable chase + compliance gates), adaptive strategy layer (signal extraction → plan next touch), LLM conversation brain (slot filling, UPL guardrails, Spanish), and swappable channel adapters (Twilio, Resend, DocuSeal, Vapi). Intake state is channel-agnostic — one `IntakeRecord` and one workflow whether the lead came from a missed call, web form, Clio Grow stub, or designed web-chat ingress. Six caller scenarios (responsive, ghosting, hostile, wrong number, legal advice, Spanish) run via CLI and tests. Live integrations are wired where sandbox access allowed; Clio Grow and PSTN from India are stubbed with documented workarounds.

**How I built it.** I worked in phases with Cursor Agent: I owned problem framing, architecture, and verification; AI handled boilerplate and implementation drafts. Phase 1 — chose Temporal + CQRS + strategy/brain split. Phase 2 — core slice (lead → workflow → brain → mock channels). Phase 3 — refactored from cadence-first to plan-driven adaptive strategy after comparing early work to the brief’s bar. Phase 4 — swapped to Resend, DocuSeal, Anthropic, and live Clio Manage. Phase 5 — live E2E testing (ngrok, Twilio webhooks), caught and fixed routing bugs, LLM timeouts, and delivery failures. Phase 6 — demo acceleration (`DEMO_FAST_CADENCE`, `run_scenario.py all`) and this reasoning trail.

**Design, architecture, and decision challenges I faced.**

- **Script vs specialist.** The hardest design call was separating _what to do next_ (strategy) from _what to say_ (brain). I initially leaned cadence-first; I corrected that when it clearly failed the brief — cadence is now fallback for ghosting only.
- **Auditability vs adaptiveness.** I rejected a pure LLM planner for channel selection; rules + optional LLM signal extraction keeps compliance decisions deterministic while still adapting to SMS-hot, hostile, and opt-out cues.
- **Channel-agnostic intake.** I resisted building channel-specific flows. Ingress is a payload difference (`trigger`, `consent.origin`, slots) — same engine, different first plan.
- **Under-specified integrations.** No Clio Grow API in timebox → fixture preserves ingress semantics. Developer in India, US PSTN → simulated voice harness shares the same brain endpoint as live Vapi. Twilio trial rejected long emoji SMS (error 30044) even when API returned 201 — I had to query delivery logs, not trust HTTP status alone.
- **Live E2E vs demo speed.** 14-day waits are real in production; I added `DEMO_FAST_CADENCE` and a scenario CLI so I can run all six paths in minutes without waiting days.

I position this as roughly **65–70% of the ideal product** with **sharp reasoning** — architecture and adaptive behaviors are solid; deep persona modeling, contact-window enforcement, and live Clio Grow ingest remain on my two-week roadmap below.

---

### What works today

| Capability                                                                                       | Status                                     |
| ------------------------------------------------------------------------------------------------ | ------------------------------------------ |
| Channel-agnostic intake state (missed call, web form, Clio Grow stub, web chat ingress)          | ✅ Same engine, different first plan       |
| Adaptive strategy (SMS-hot → stop calling, hostile → de-escalation, ghosting → cadence fallback) | ✅ Rules + optional LLM signals            |
| Durable 14-day chase (Temporal, timer-vs-inbound race)                                           | ✅ With `DEMO_FAST_CADENCE` for testing    |
| LLM brain (slots, UPL guardrails, Spanish, message goals)                                        | ✅ Anthropic native + OpenAI-compatible    |
| Six caller scenarios                                                                             | ✅ CLI + pytest                            |
| Live SMS via Twilio                                                                              | ✅ Wired; trial delivery limits discovered |
| Live email via Resend                                                                            | ✅ Wired                                   |
| eSign via DocuSeal (self-hosted)                                                                 | ✅ Wired + webhook                         |
| Clio Manage write-back                                                                           | ✅ Real sandbox token verified             |
| Clio Grow ingest                                                                                 | ⚠️ Fixture stub only                       |

## 2. Problem Statement & The Bar

This is how I read the problem and the bar I held myself to.

Law firms receive **partial leads** — someone started intake (web form, missed call, Clio Grow, chat) but dropped off. A great human intake specialist:

- Reads who they're talking to
- Adapts channel and timing ("they texted back — stop calling")
- Handles unscripted situations (hostile, legal advice, wrong number, Spanish-only)
- Chases persistently but compliantly over 14 days

**The bar is not a fixed path.** It's something that genuinely understands context and responds the way a thoughtful human would.

### How I interpreted the brief

| Brief requirement                  | My response                                                                      |
| ---------------------------------- | -------------------------------------------------------------------------------- |
| Adaptive, context-aware            | Strategy layer + signal extraction + message goals injected into brain           |
| Channel-agnostic intake            | Single `IntakeRecord`; ingress only changes `trigger` / `consent.origin` / slots |
| No web-chat UI                     | Designed ingress via `trigger: web_chat` + API; no UI built                      |
| Clio sandbox                       | Manage API live; Grow stubbed with fixture + same `create_lead` path             |
| Voice/SMS/email/eSign — your setup | Twilio, Resend, DocuSeal self-hosted, Vapi; stub where blocked                   |
| Six scenarios                      | All runnable via `scripts/run_scenario.py` + tests                               |
| Reasoning trail                    | This document                                                                    |
| How I worked with AI               | Phased delegation; corrections and what I threw out                              |

**Gaps in the brief treated as part of the task:** I did not ask whether to use Temporal vs cron, rules vs pure LLM planner, or Postmark vs Resend — I decided based on auditability and sandbox access.

---

## 3. Design Philosophy

I split the system into four layers on purpose — each layer maps to a decision a human intake specialist makes vs. a system constraint I need to enforce in software.

### Why I chose this decomposition

| Layer                       | Responsibility                                              | Why separate?                                   |
| --------------------------- | ----------------------------------------------------------- | ----------------------------------------------- |
| **Orchestrator** (Temporal) | When to act, durability, compliance gates, wait for inbound | Legal chase spans days; must survive restarts   |
| **Strategy**                | Who this person is, next channel/timing/goal                | Humans adapt the _plan_, not just the script    |
| **Brain** (LLM)             | What to say on this turn                                    | Language is hard; policy is auditable elsewhere |
| **Channels**                | How to send (voice/SMS/email/eSign)                         | Swap providers without touching logic           |

### Core invariants I enforce

1. **Planner proposes, gates dispose** — I never let the LLM override compliance (opt-out, quiet hours, attempt caps)
2. **Event log is source of truth** — I rebuild `IntakeRecord` from append-only events (CQRS)
3. **Channel-blind brain** — the LLM emits intent + content; my adapters handle delivery
4. **Cadence is fallback, not driver** — I only use the fixed 9-step schedule when no engagement signals exist

### What I deliberately did NOT build

- Web chat UI (brief said not to — only ingress design)
- LLM-driven compliance decisions (too risky for legal)
- Pure LLM planner for channel selection (not auditable)
- Real Clio Grow API poll (no stable public read API in timebox; fixture preserves ingress semantics)

---

## 4. Phased Approach: Planning → Execution

I split the work into phases and used Cursor Agent differently in each — **planning and judgment stayed with me; execution was delegated**.

### Phase 1 — Assessment & architecture (my call)

**My goal:** Frame the problem against the bar; choose decomposition before coding.

| Decision                     | Why                                                                     |
| ---------------------------- | ----------------------------------------------------------------------- |
| Temporal over cron/sleep     | Multi-day chase, inbound interrupts timer, must survive restarts        |
| CQRS event log               | Compliance audit, cross-channel history, rebuild projection             |
| Strategy separate from brain | "Stop calling after SMS reply" is a _plan_ change, not a wording change |
| Cadence as fallback only     | Ghosting needs a schedule; engaged leads should not follow fixed script |

**Cursor's role:** Drafted initial README/architecture diagrams; I rejected cadence-as-primary-driver before implementation completed.

### Phase 2 — Core slice (delegated implementation)

**My goal:** Working path: create lead → workflow → brain → mock channels → disposition.

**What I built:** Postgres + Temporal + FastAPI + `IntakeRecord` + projection + basic workflow loop + mock adapters + scenario tests.

**Cursor's role:** Boilerplate (SQLAlchemy models, activity stubs, pytest fixtures). **I verified:** disposition transitions, event reducer correctness.

### Phase 3 — Adaptive strategy layer (hybrid: spec + implementation)

**My goal:** Close the gap between "fixed cadence" and "human specialist."

**What triggered this:** The assessment bar explicitly rejects scripts. My initial build was cadence-first — I identified that as the main architectural miss and directed a refactor.

**What I built:**

- `strategy.py` — `extract_signals_rules`, `plan_next_touch`, `plan_initial_touch`, cadence fallback
- `signal_extractor.py` — hybrid LLM for ambiguous inbound (5s timeout, rules win on compliance)
- Extended `EngagementState` — responsiveness, do_not_call, engagement_mode, last_plan
- Refactored `workflow.py` — plan-driven loop with gate retry and inbound re-plan
- Brain — `message_goal`, `plan_reason`, `constraints` in prompts

**Cursor's role:** Implemented per my spec. **I verified:** SMS-hot → no voice (unit tests), ghosting still hits cadence fallback, ingress matrix plans.

### Phase 4 — Integration swaps & production wiring

**My goal:** Replace stubs with real providers where I had sandbox access; document the rest honestly.

| Swap  | From               | To                                       | Reason (§10)                                             |
| ----- | ------------------ | ---------------------------------------- | -------------------------------------------------------- |
| Email | Postmark           | **Resend**                               | Simpler API, strong free tier, faster signup             |
| eSign | Dropbox Sign       | **DocuSeal** (self-hosted)               | Docker-local dev, no per-envelope SaaS lock-in for demo  |
| LLM   | Remote Ollama only | **Anthropic native** + OpenAI-compatible | Reliable API key; remote Ollama unreachable in live test |
| Clio  | Mock write         | **Clio Manage** sandbox token            | Free dev account; real contact/matter write-back         |

**What I also wired:** Twilio SMS (live), Vapi voice adapter, ngrok for webhooks, DocuSeal in docker-compose.

**Cursor's role:** Adapter rewrites, config, webhook handlers. **I verified:** Clio `who_am_i`, Twilio message logs, health endpoints.

### Phase 5 — Live E2E testing & bug fixes (my verification heavy)

**My goal:** Run the real flow end-to-end — not mocks.

**What I discovered and fixed:**

| Issue                        | Symptom                              | Fix                                              |
| ---------------------------- | ------------------------------------ | ------------------------------------------------ |
| Vapi sub-app mounted at `/`  | `/api/leads` returned 404            | Register `/v1/chat/completions` on main app only |
| LLM unreachable (Ollama URL) | Temporal activity `ConnectTimeout`   | Switched to `LLM_PROVIDER=anthropic`             |
| `USE_MOCK_CHANNELS=true`     | No real SMS despite "all integrated" | Flipped to false; documented restart requirement |
| Twilio trial error **30044** | API 201 but SMS never arrives        | Message too long + emojis; trial = 1 segment max |
| `clio_grow --poll`           | Creates DB row, no workflow          | Use `POST /api/leads` for full E2E (documented)  |
| Duplicate `lead_id`          | IntegrityError on retry              | New lead_id or delete row                        |
| SMS status_callback          | Wrong URL (`llm_base_url`)           | Fixed to `public_base_url`                       |

**What I added:** `DEMO_FAST_CADENCE` — cap waits to 10s, ghosting timeout 15 min so I can test live without waiting days.

### Phase 6 — Demo acceleration & documentation

**My goal:** Runnable submission artifacts.

- `scripts/run_scenario.py all --channel sms` — all six scenarios back-to-back
- This presentation — my full reasoning trail
- My live runbook: ngrok, Twilio webhook, verified numbers, DocuSeal template ID

---

## 5. Architecture Overview

This is the architecture I landed on after Phase 1 — one workflow per lead, plan-driven, channel-agnostic.

```
┌─────────────────────────────────────────────────────────────────┐
│                        LEAD INGRESS                              │
│  POST /api/leads │ Clio Grow fixture │ webhooks (SMS/email/esign)│
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
│  extract_signals  │                   │   Anthropic / Ollama    │
│  plan_next_touch  │── message_goal ──▶│   tools + UPL filter    │
│  cadence fallback │                   └───────────┬─────────────┘
└─────────┬─────────┘                               │
          │                                         ▼
          │                               ┌─────────────────────────┐
          │                               │   CHANNEL ADAPTERS      │
          └──────────────────────────────▶│ Twilio / Resend / Vapi  │
                                          │ DocuSeal / mock         │
                                          └─────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                     CQRS STATE (PostgreSQL)                      │
│   events (append-only) ──▶ leads projection (IntakeRecord)      │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
                    Clio Manage write-back (terminal milestones)
```

### Request flow (how I wired a single outbound touch)

1. My workflow calls `plan_next_touch(lead_id)`
2. My strategy layer reads the engagement model → channel, message_goal, wait_seconds, reason
3. My compliance gates veto if opt-out / quiet hours / caps
4. `execute_outbound_touch` → brain generates content → my adapter sends
5. `extract_signals` updates the person model
6. The workflow sleeps or races against an inbound signal
7. On inbound → `process_inbound` → brain reply → I re-plan from scratch

---

## 6. Data Model & CQRS (Channel-Agnostic)

I designed intake state so it **does not assume the lead came from a phone call.** The same `IntakeRecord` backs missed calls, web forms, Clio Grow partials, and (designed) web chat.

### IntakeRecord

| Area             | Key fields                                                                                                                                                   |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Identity**     | name, phone[], email[]                                                                                                                                       |
| **Consent**      | origin (inbound/outbound), channels_allowed, ai_voice_consent, opt_out                                                                                       |
| **Intake slots** | accident_date, location, type, injuries, treatment, insurance, prior_attorney                                                                                |
| **Engagement**   | sentiment, attempts_by_channel, channel_preference, responsiveness, do_not_call, engagement_mode, contact_window, last_plan, cadence_step_index, **trigger** |
| **Disposition**  | NEW → CHASING → ENGAGED → INTAKE_COMPLETE → RETAINER_SENT → SIGNED                                                                                           |
| **Terminal**     | OPTED_OUT, HANDOFF, GAVE_UP, WRONG_NUMBER                                                                                                                    |
| **Retainer**     | envelope_id, status, signed_pdf_ref                                                                                                                          |

### Event-sourced projection

- **`events`** — append-only log
- **`leads`** — materialized projection
- **`apply_event()`** — reducer in `src/store/projection.py`

**Why I chose CQRS:** I needed a compliance audit trail, cross-channel conversation history, and the ability to rebuild state after bugs.

---

## 7. Adaptive Strategy Layer

This is the layer I added in Phase 3 to meet the "human specialist, not script" bar.

**Files I wrote:** `src/orchestrator/strategy.py`, `src/orchestrator/signal_extractor.py`

### Closed loop (my "human specialist" mechanism)

```
observe → extract signals → update person model → plan next touch → brain executes → repeat
```

This is how **my system changes strategy mid-chase** — not just the next sentence.

### Signal extraction

**Rules (always on):**

| Signal                     | Effect                                             |
| -------------------------- | -------------------------------------------------- |
| SMS inbound                | `responsiveness[sms]=hot`, prefer SMS              |
| Reply within 5 min         | `hot` on that channel                              |
| "Text me" / "stop calling" | `do_not_call=true`, SMS only                       |
| Hostile keywords           | `engagement_mode=hostile_open`, de-escalation path |
| 3+ outbound, no inbound    | `engagement_mode=ghosting` → cadence fallback      |
| "Call after 5" / "evening" | `contact_window` set (extracted, not yet enforced) |

**Optional LLM** (`STRATEGY_LLM_SIGNALS=true`, 5s timeout): ambiguous inbound > 20 chars → JSON patch; **rules win** on opt-out / do_not_call.

### Planner rule priority

1. Terminal (14-day timeout, max attempts) → give up
2. `do_not_call` or SMS-hot → **never voice**
3. Hostile, not opted out → empathy SMS, 48h wait
4. Intake complete → send retainer
5. Engaged + partial slots → gap_fill on best channel
6. **Fallback:** 9-step cadence curve

Every plan emits `strategy.planned` with auditable `reason` (e.g. `adaptive:sms_hot_responsive`).

### Message goals (passed to brain)

| Goal               | When used                    |
| ------------------ | ---------------------------- |
| `instant_reply`    | Missed call / inbound origin |
| `warm_intro`       | Empty web form               |
| `confirm_slots`    | Partial web form             |
| `gap_fill`         | Engaged, missing slots       |
| `empathy_check_in` | Hostile but not opted out    |
| `soft_reengage`    | Clio Grow stale lead         |
| `call_attempt`     | Cadence fallback voice       |
| `loss_aversion`    | Day 7 ghosting               |
| `send_retainer`    | Intake complete              |
| `give_up`          | 14-day timeout               |

---

## 8. Conversation Brain & LLM

**Files I wrote:** `src/brain/agent.py`, `src/brain/llm.py`, `src/brain/upl_filter.py`

### Brain role (how I configured it)

I configured the brain as an automated assistant for Marigold Injury Law. **It is not a lawyer.** It collects intake slots, adapts tone, and never gives legal advice.

### Tools (function calling)

`update_slot`, `derive_sol_date`, `send_intake_link`, `send_retainer`, `check_retainer_status`, `escalate_to_human`, `mark_wrong_number`, `set_opt_out`, `set_language`

### Guardrails I put in place

1. Prompt-level UPL rules
2. Deterministic UPL filter (`upl_filter.py`) — my regex backstop
3. Sentiment detection — keyword hostile/distressed
4. Opener disclosure — automated assistant, not attorney, calls recorded

### How I inject strategy into the brain

```
Strategy for this touch: gap_fill
Reason: adaptive:sms_hot_responsive
Constraints: under 300 chars, no voice mention
Do not re-ask fields already in Collected info unless confirming.
```

---

## 9. Temporal Orchestrator

I use Temporal because a PI chase spans days and must survive restarts — cron would not handle inbound interrupts cleanly.

**Files:** `src/orchestrator/workflow.py`, `src/orchestrator/activities.py`, `src/worker.py`

### Workflow ID

`chase-{lead_id}` on task queue `outbound-chase`

### Signals

| Signal          | Effect                                       |
| --------------- | -------------------------------------------- |
| `inbound_event` | SMS/email/voice reply — **interrupts timer** |
| `stop_chase`    | External stop                                |
| `opt_out`       | Immediate opt-out                            |

### Compliance gates (hard veto)

| Gate                     | Behavior                                          |
| ------------------------ | ------------------------------------------------- |
| Opt-out                  | Terminal OPTED_OUT                                |
| Quiet hours              | 8 AM – 9 PM Phoenix                               |
| Voice / SMS / email caps | 3 / 5 / 3 per channel                             |
| Total cap                | 10 attempts                                       |
| Chase timeout            | 14 days → GAVE_UP (15 min in `DEMO_FAST_CADENCE`) |
| AI voice consent         | Required unless `DEMO_AI_VOICE_CONSENT=true`      |

### Demo fast cadence (how I test without waiting days)

```bash
DEMO_FAST_CADENCE=true
DEMO_WAIT_SECONDS_CAP=10      # max seconds between touches
DEMO_CHASE_TIMEOUT_MINUTES=15 # ghosting give-up window
```

In production I keep the full cadence when `DEMO_FAST_CADENCE=false`.

### Cadence fallback (ghosting)

| Step | Channel | Goal                    | Backoff (prod) |
| ---- | ------- | ----------------------- | -------------- |
| 0    | SMS     | instant_reply           | 0              |
| 1    | Voice   | call_attempt            | 5 min          |
| 2    | SMS     | follow_up               | 1 hr           |
| 3    | Voice   | call_attempt (alt hour) | 1 day          |
| 4    | Email   | intake_link             | 2 days         |
| 5    | Voice   | call_attempt            | 4 days         |
| 6    | SMS     | loss_aversion           | 7 days         |
| 7    | Email   | final                   | 10 days        |
| 8    | —       | give_up                 | 14 days        |

**Voice AMD=machine** → unique voicemail + paired SMS.

---

## 10. Integrations

These are the providers I chose, why I chose them, and what actually worked in my live tests.

### Summary table

| Integration          | My choice                  | Why I chose this over alternatives                                                 | My status                    |
| -------------------- | -------------------------- | ---------------------------------------------------------------------------------- | ---------------------------- |
| **Orchestration**    | Temporal                   | Durable multi-day chase, signal/timer race                                         | ✅ Live                      |
| **Database**         | PostgreSQL + CQRS          | Audit + rebuild                                                                    | ✅ Live (port 5434)          |
| **LLM**              | Anthropic                  | Reliable API key; tool calling; my remote Ollama timed out in live E2E             | ✅ Live                      |
| **SMS**              | Twilio                     | Industry standard; brief allows own setup                                          | ✅ Live (trial limits)       |
| **Email**            | **Resend**                 | Replaced Postmark — simpler REST API, fast signup, `onboarding@resend.dev` for dev | ✅ Live                      |
| **eSign**            | **DocuSeal** (self-hosted) | Replaced Dropbox Sign — Docker local, open/self-host, webhook API                  | ✅ Live (template + Pro API) |
| **Voice**            | Vapi + custom LLM URL      | Brief PSTN path; brain served from same FastAPI app                                | ✅ Wired; India dev uses sim |
| **Clio Manage**      | Clio API v4                | Brief sandbox; write contact/matter/note on terminal                               | ✅ Token verified            |
| **Clio Grow**        | Fixture JSON               | No Grow read API integrated in timebox                                             | ⚠️ Stub                      |
| **Inbound webhooks** | ngrok                      | localhost not reachable by Twilio/DocuSeal                                         | ✅ Used in live test         |
| **Tunnel**           | ngrok                      | Required for `/webhooks/sms`, `/webhooks/esign`, Vapi LLM URL                      | ✅                           |

### Resend (email) — why I switched

**What I tried first:** Postmark in my initial scaffold.  
**What blocked me:** Extra vendor setup; stale env vars caused validation errors after the swap.

**Why I chose Resend:**

- Single REST API, good DX
- Free tier sufficient for demo
- `onboarding@resend.dev` works without domain verification for first sends
- Tags support `lead_id` for future inbound correlation

### DocuSeal (eSign) — why I switched

**What I tried first:** Dropbox Sign adapter.  
**Why I chose DocuSeal:**

- Self-hosted via `docker compose up docuseal` — full local loop without SaaS billing
- Open submission API + `submission.completed` webhook maps cleanly to `retainer_signed` event

### Anthropic (LLM) — why I added a native provider

**What I tried first:** Remote Ollama at `LLM_BASE_URL` with a Claude model name (invalid combo).  
**What I saw:** Temporal workflow failed with `APITimeoutError` / `ConnectTimeout`.  
**What I did:** Added `src/brain/llm.py` with `LLM_PROVIDER=anthropic` — uses my `sk-ant-...` key directly, no endpoint. I kept the OpenAI-compatible path for Ollama/local.

### Clio Manage — why I wired it live; Grow — why I stubbed

**Clio Manage:** I signed up for a free developer account; my OAuth/token → `who_am_i` verified. Write-back on SIGNED/GAVE_UP/etc. is the firm-facing payoff I wanted to prove.  
**Clio Grow:** Ingress semantics matter to me (`trigger: clio_grow_stub` → soft_reengage plan). I used fixture `partial_leads.json` + a poll script to preserve that without Grow API scope negotiation in the timebox. **Next for me:** Zapier/webhook or Grow API → same `POST /api/leads`.

### Twilio — what I learned in live testing

My flow **was working** (LLM → Twilio API 201) but messages **failed delivery** with error **30044** (Trial Message Length Exceeded): my LLM output + emoji + the trial prefix exceeded the 1-segment trial limit. **My workarounds:** upgrade Twilio, shorten SMS, strip emojis, or use a US verified number.

---

## 11. Channel Adapters

I use a single adapter interface so I can swap providers without touching orchestration or strategy.

**Pattern:** `src/channels/base.py` → mock / real via config.

| Channel | Mock                    | Production      | Config                       |
| ------- | ----------------------- | --------------- | ---------------------------- |
| Voice   | `SimulatedVoiceAdapter` | Vapi PSTN       | `VOICE_MODE=simulated\|vapi` |
| SMS     | `MockSMSAdapter`        | Twilio          | `USE_MOCK_CHANNELS=false`    |
| Email   | `MockEmailAdapter`      | **Resend**      | `RESEND_API_KEY`             |
| eSign   | Mock envelope           | **DocuSeal**    | `DOCUSEAL_*`                 |
| Clio    | Audit-only if no token  | Clio Manage API | `CLIO_ACCESS_TOKEN`          |

I set `USE_MOCK_CHANNELS=false` when I want all real adapters (keys required).

---

## 12. Ingress Matrix (No Rework Per Source)

I deliberately built one engine, one workflow, one brain — only the **first plan** changes by ingress:

| Source                 | API payload                               | First plan          | Notes                       |
| ---------------------- | ----------------------------------------- | ------------------- | --------------------------- |
| **Missed call**        | `consent.origin: "inbound"`               | SMS `instant_reply` | Acknowledge missed call     |
| **Web form (partial)** | `trigger: "web_form"` + slots             | SMS `confirm_slots` | Confirm known, ask one gap  |
| **Web form (empty)**   | `trigger: "web_form"`                     | SMS `warm_intro`    | Standard opener             |
| **Clio Grow**          | `trigger: "clio_grow_stub"`               | SMS `soft_reengage` | 24h wait (10s in demo-fast) |
| **Web chat**           | `trigger: "web_chat"` + conversation seed | SMS `gap_fill`      | No UI; same API             |

**My example — Clio Grow style live test:**

```json
POST /api/leads
{
  "lead_id": "live-test-003",
  "trigger": "clio_grow_stub",
  "identity": {"name": "Gulshan", "phone": ["+1..."], "email": ["..."]},
  "consent": {"origin": "outbound", "channels_allowed": ["voice","sms","email"], "ai_voice_consent": true},
  "intake_slots": {"accident_date": {"value": "2026-03-01", "confidence": 0.6}}
}
```

I did not fork code per channel — only the ingress payload differs.

## 14. Six Caller Scenarios

These are the six paths I committed to demo and test:
| Scenario | What I set out to prove | Command |
| ---------------- | -------------------------------------------------- | -------------------------------------------------------------- |
| **Responsive** | SMS happy path, slot capture, adaptive SMS plan | `python3 scripts/run_scenario.py responsive --channel sms` |
| **Ghosting** | Cadence fallback, voicemail + paired SMS, `[PLAN]` | `python3 scripts/run_scenario.py ghosting` |
| **Hostile** | De-escalation, opt-out | `python3 scripts/run_scenario.py hostile --channel voice` |
| **Wrong number** | Immediate suppression | `python3 scripts/run_scenario.py wrong_number --channel voice` |
| **Legal advice** | UPL redirect | `python3 scripts/run_scenario.py legal_advice --channel voice` |
| **Spanish** | Language parity | `python3 scripts/run_scenario.py spanish --channel sms` |
| **All six** | Back-to-back, no Temporal waits | `python3 scripts/run_scenario.py all --channel sms` |

**Caller scripts:** `tests/sim/caller_scripts.py`  
**Voice driver:** `tests/sim/voice_driver.py` — real LLM, simulated PSTN

---

## 15. Stubs, Blockers & Workarounds

Where I hit walls, I stubbed honestly and documented the production path.

| Component                   | Did I stub it?     | What blocked me                            | How I worked around it                                                   |
| --------------------------- | ------------------ | ------------------------------------------ | ------------------------------------------------------------------------ |
| **Clio Grow**               | Yes — fixture JSON | Grow API not integrated in timebox         | Same `create_lead` + `clio_grow_stub` trigger; real Grow → webhook later |
| **Clio Manage**             | No (with token)    | Hardcoded practice_area / custom_field IDs | Map to sandbox IDs in `clio.py`                                          |
| **Clio OAuth callback**     | Not built          | Used manual token / approval URL           | Paste `CLIO_ACCESS_TOKEN`                                                |
| **Voice PSTN from India**   | Simulated default  | US Twilio/Vapi numbers                     | `SimulatedVoiceAdapter` + same brain endpoint for all scenarios          |
| **Vapi AMD webhook**        | Partial            | Production AMD path                        | Mock AMD per lead in sim                                                 |
| **Email inbound**           | Generic webhook    | Resend inbound not fully wired             | Simulate inbound API                                                     |
| **Web chat UI**             | By design          | Brief says don't build                     | Ingress via API only                                                     |
| **Twilio trial SMS**        | N/A                | Error 30044 — message too long             | Shorten SMS, no emoji, upgrade account                                   |
| **International SMS (+91)** | N/A                | Trial geo + verification                   | Verify number in Twilio; US number easier                                |
| **DocuSeal API**            | N/A                | Pro license on self-hosted                 | Mock esign or cloud DocuSeal                                             |

**Examples of resourcefulness I want to highlight:**

- I couldn't call US PSTN reliably from India → I built a simulated voice harness that uses the same brain endpoint as live Vapi
- I couldn't wait 14 days in a demo → I added `DEMO_FAST_CADENCE` + `run_scenario.py ghosting` loops without Temporal sleep
- Twilio returned 201 but nothing arrived on my phone → I queried Twilio logs myself, found 30044, and documented the root cause

---

## 16. How to Run (Demo & Live E2E)

This is how I run and demo the project today.

### Prerequisites

```bash
pip install -e ".[dev]"
cp .env.example .env
# Key vars: DATABASE_URL (5434), LLM_PROVIDER, TWILIO_*, RESEND_*, DOCUSEAL_*, CLIO_ACCESS_TOKEN
```

### Infrastructure

```bash
docker compose up -d postgres temporal temporal-ui docuseal docuseal-postgres
python3 -m src.main --mode worker   # terminal 1
python3 -m src.main --mode server   # terminal 2
```

### Fast scenario demo (no real channels, ~10 min)

```bash
python3 scripts/run_scenario.py all --channel sms
```

### Live E2E (real SMS) — my checklist

1. I set `USE_MOCK_CHANNELS=false`, `LLM_PROVIDER=anthropic`
2. I run `ngrok http 8000` → set `PUBLIC_BASE_URL`
3. In Twilio → my phone number → webhook `https://<ngrok>/webhooks/sms`
4. I verify the recipient number in Twilio (trial)
5. I `POST /api/leads` with **my personal phone** in `identity.phone` (not the Twilio number)
6. I use `DEMO_FAST_CADENCE=true` for 10s gaps
7. I reply to SMS from my phone → my workflow wakes via webhook

### How I monitor a run

- Temporal UI: http://localhost:8081 → `chase-{lead_id}`
- Lead state: `GET /api/leads/{lead_id}`
- Twilio logs: delivery status / error codes

---

## 17. Testing

These are the tests I rely on before I demo:

```bash
pytest tests/test_strategy.py tests/test_scenarios.py tests/test_projection.py -v
pytest tests/integration/ -v
pytest tests/integration/ -m llm -v   # requires LLM
```

| Test file                   | What I use it to prove                             |
| --------------------------- | -------------------------------------------------- |
| `test_strategy.py`          | SMS-hot → no voice, ingress plans, demo-fast waits |
| `test_strategy_adaptive.py` | DB-persisted signals                               |
| `test_workflow_ghosting.py` | Voicemail + paired SMS                             |
| `test_voice_scenarios.py`   | Voice + LLM (@llm)                                 |
| `test_async_scenarios.py`   | SMS + Spanish (@llm)                               |

---

## 18. Where the Loop Breaks (My Honest Gaps)

### Where I still fall short of "best human intake specialist"

1. **My strategy is rules + keywords** — not deep subtext ("sounds hesitant but interested")
2. **Conversation memory is thin** across channels in my projection
3. **I have no eval harness yet** — I can't score "feels human" at scale
4. **I extract `contact_window` but don't enforce it** in scheduling
5. **I don't use email-opened signal** in my planner yet
6. **Clio Grow is not live for me** — fixture only
7. **SMS trial delivery** — I still need segment-aware truncation / no emoji for production Twilio
8. **My voice CLI bypasses Temporal** in the scenario script (activities called directly)

### Where I'm confident the adaptive loop works

- SMS reply → I stop calling, prefer SMS (I verified in tests + live)
- Hostile → my empathy path before opt-out
- Ghosting → my cadence fallback with voicemail + paired SMS
- Ingress matrix → different first touch without code forks
- Inbound signal **interrupts** my Temporal timer (real webhook or simulate)

---

## 20. Two-Week Roadmap

If I had two more weeks, this is what I would build next:

| Priority | What I would do                                                         |
| -------- | ----------------------------------------------------------------------- |
| 1        | **Clio Grow live ingest** — webhook or API → `POST /api/leads`          |
| 2        | **Persona eval harness** — 20 synthetic leads, expected plan assertions |
| 3        | **Contact window enforcement** — "call after 5pm" actually deferred     |
| ...      | ...                                                                     |

---
