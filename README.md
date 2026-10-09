# VoiceOps — Multi-Agent Voice AI Operations Platform

Voice-first AI operations assistant: users speak or type natural-language requests, a supervisor agent delegates to specialist agents (calendar, email, weather, research, tasks, knowledge), specialist agents call real tools, and sensitive actions pause for explicit human approval with a full audit trail.

> **Status: Parts 1–8 complete.** JWT authentication with per-user data isolation, per-user rate limiting, safe execution tracing, prompt-injection defenses, a 114-task agent evaluation harness, Docker + docker-compose, and GitHub Actions CI. The Google Cloud Run deployment from the original roadmap is **not** yet done; deployment notes below describe the Docker path that has been written but **not executed end-to-end**.

---

## Project overview

VoiceOps turns speech or text into reliable, auditable operations:

- **Chat or speak** a request ("Am I free tomorrow at 3pm?", "Draft an email to my manager").
- The **supervisor** routes to specialist **agents**; each agent plans and calls **real tools**.
- Mutating actions (send email, create/update/delete calendar events) **require human approval** — the agent can only enqueue them.
- Uploaded **documents** (PDF/TXT/DOCX) power a grounded knowledge agent with page-level citations.
- Everything is persisted per-user, traced with safe metadata, and observable.

## Architecture

```
┌───────────────┐     ┌──────────────────────────────────────────────────┐
│   Frontend    │     │                    Backend (FastAPI)             │
│ static HTML/  │HTTP │  ┌────────── auth middleware ────────────────┐   │
│ CSS/JS + mic  │────▶│  │ JWT check · rate limit · request-id       │   │
└───────────────┘     │  └───────────────────┬──────────────────────┘   │
                      │                      ▼                          │
                      │   ┌────────────────────────────────────────┐    │
                      │   │            Supervisor Agent            │    │
                      │   │  routes → specialist agents (LangGraph)│    │
                      │   └───┬──────┬──────┬──────┬──────┬──────┬──┘    │
                      │       ▼      ▼      ▼      ▼      ▼      ▼       │
                      │  Research Task Weather Calendar Email Knowledge │
                      │       │      │      │      │      │      │       │
                      │       ▼      ▼      ▼      ▼      ▼      ▼       │
                      │   [web_search][tasks][weather][calendar][gmail]  │
                      │                                  [RAG: pgvector] │
                      └──────────┬───────────────────────────────────────┘
                                 ▼
                 PostgreSQL (users, conversations, tasks, documents,
                 embeddings, approvals, audit log)  ·  Redis (reserved)
```

### Request flow (one chat turn)

```
User message
  → POST /api/v1/chat (Bearer token, per-user rate limit)
  → conversation resolved/created (ownership-checked)
  → history + long-term memories injected as context
  → Supervisor: route (structured output, no free-form CoT)
  → Specialist agent: plan tool calls (validated args, allow-listed tools)
  → Tools execute (retries on transient failure; approval-gated tools
    enqueue a pending approval instead of executing)
  → sanitized tool summaries → supervisor final answer
  → messages, agent run + tool-call trace persisted
  → JSON response with session_id + safe trace events
```

### Voice flow

Voice uses the **same pipeline**: `mic → Deepgram STT → /api/v1/voice → supervisor pipeline → answer → ElevenLabs TTS → speaker`. There is no separate voice bot; voice and text share routing, tools, approvals, and memory.

## Features

- **Multi-agent orchestration** — supervisor + 6 specialists via LangGraph; step budget prevents infinite delegation.
- **17 real tools** — weather (Open-Meteo), web search (Tavily), task CRUD (DB-backed), Google Calendar, Gmail, RAG knowledge tools.
- **Human-in-the-loop approvals** — mutating actions create pending approval rows; execution happens only after an explicit user decision, with an audit trail.
- **Personal knowledge agent (RAG)** — upload PDF/TXT/DOCX → extract → chunk → embed (Gemini `text-embedding-004`, 768-d) → pgvector retrieval → grounded answers with `Source:\nfile.pdf\nPage N` citations; irrelevant queries get "not found in your documents", never speculation.
- **Authentication** — register/login, PBKDF2 (200k iterations) password hashing, HS256 JWT bearer tokens; every data endpoint is protected. The frontend stores the token and signs all requests, auto-clearing on 401.
- **Authorization** — strict per-user isolation: conversations, tasks, memories, documents, approvals, and audit logs are scoped to the authenticated user; foreign IDs are indistinguishable from missing ones (404).
- **Rate limiting** — per-user sliding-window limits on chat (30/min), voice (15/min), uploads (10/min); 429 + `Retry-After`.
- **Observability** — `X-Request-ID` on every response and in error envelopes; structured access logs (method/path/status/latency only); agent runs and tool calls stored as safe metadata.
- **Error handling** — one JSON error envelope for all failures; LLM failures surface as 502 with fallback messaging; tool errors are contained and summarized to the user.
- **Prompt-injection defenses** — external content (web results, document text) is sanitized and wrapped as untrusted data before reaching any LLM.

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI, pydantic-settings, SQLAlchemy 2 (async) |
| Frontend | Vanilla HTML / CSS / JavaScript (no frameworks) |
| Database | PostgreSQL + pgvector (embeddings), Alembic migrations |
| AI | LangGraph orchestration, Gemini API, Deepgram STT, ElevenLabs TTS |
| Auth | Hand-rolled HS256 JWTs + PBKDF2 (no extra runtime deps) |
| Infrastructure | Docker, docker-compose, GitHub Actions |

## Project structure

```
voiceops/
├── frontend/
│   ├── index.html, css/, assets/
│   └── js/               # api.js, main.js, approvals.js, documents.js, voice.js
├── backend/
│   ├── app/
│   │   ├── api/          # routers: chat, voice, data, documents, approvals, auth_*
│   │   ├── agents/       # LangGraph graph, supervisor, structured schemas, prompts
│   │   ├── tools/        # 17 tools + registry (per-agent allow-lists)
│   │   ├── services/     # persistence, task store, approvals, RAG, LLM providers
│   │   ├── models/       # SQLAlchemy ORM models
│   │   ├── schemas/      # Pydantic request/response schemas
│   │   ├── database/     # engine/session management
│   │   └── core/         # config, security (JWT/PBKDF2), rate_limit, errors,
│   │                     # content_guard (prompt-injection), logging
│   ├── migrations/       # Alembic: 0001 core · 0002 oauth · 0003 RAG · 0004 auth
│   ├── eval/             # dataset.json (114 tasks) + run_eval.py harness
│   └── tests/            # pytest suite (81 tests, SQLite; no services needed)
├── docker-compose.yml    # frontend · backend · postgres(pgvector) · redis
├── .github/workflows/    # CI: ruff lint · pytest · docker build
└── .env.example          # copy to .env — never commit .env
```

## Setup

### 1. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]" "pydantic[email]"

cp ../.env.example ../.env         # then edit values if needed
uvicorn app.main:app --reload --port 8000
```

- Swagger UI: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/api/v1/health

### 2. Frontend

No build step:

```bash
cd frontend
python -m http.server 3000
# → http://127.0.0.1:3000
```

### 3. Database (Postgres + pgvector)

```bash
docker compose up -d postgres redis
cd backend
../.venv/Scripts/python -m alembic upgrade head
```

### 4. Docker (full stack)

```bash
docker compose up --build
# frontend: http://localhost:3000 · backend: http://localhost:8000/docs
```

Compose runs `alembic upgrade head` before starting the API. The Postgres service uses the `pgvector/pgvector` image. **Note:** the compose stack has not yet been executed in the development environment (no Docker available locally); it is validated only by `docker compose config` semantics and code review. Report actual results on first run.

### 5. Tests

```bash
# Backend: 81 tests, SQLite, no services needed
cd backend && python -m pytest -q

# Frontend: node:test integration tests (Node 22+, zero dependencies)
#   - auth token lifecycle (persist/attach/401-clear)
#   - error-envelope surfacing, multipart auth headers
#   - route parity: every /api/v1 path the frontend calls exists in the backend
node --test frontend/tests/api.test.mjs
```

The dashboard requires sign-in (Settings → Account): register or log in once; the JWT is stored in localStorage and attached to every API call.

## Environment variables

Copy `.env.example` to `.env` and fill in. All config is environment-only (pydantic-settings) — no hardcoded secrets anywhere in the repo. Keys: `GEMINI_API_KEY` (LLM + embeddings), `TAVILY_API_KEY` (web search), `DEEPGRAM_API_KEY` / `ELEVENLABS_API_KEY` (voice), `GOOGLE_CLIENT_ID/SECRET` (Calendar/Gmail OAuth), `TOKEN_ENCRYPTION_KEY` (OAuth tokens at rest), `APP_SECRET_KEY` (JWT signing — **required in production**), Postgres/Redis URLs, CORS origins, rate-limit values. See [.env.example](.env.example) for the full annotated list.

## Local development

1. `docker compose up -d postgres redis`
2. `cd backend && alembic upgrade head && uvicorn app.main:app --reload --port 8000`
3. `cd frontend && python -m http.server 3000`
4. Register a user in the dashboard, connect Google in Settings, start chatting.

## API documentation

Interactive OpenAPI docs at `/docs` (or `/openapi.json`). Highlights:

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/v1/auth/register`, `/auth/login` | POST | Create account / get JWT |
| `/api/v1/chat` | POST | One supervised agent turn |
| `/api/v1/conversations`, `/conversations/{id}` | GET | History |
| `/api/v1/tasks` (+`/{id}/complete`) | GET/POST | Task CRUD |
| `/api/v1/memories` | GET/POST | Long-term memory |
| `/api/v1/documents` (+`/search`, `/{id}`) | POST/GET/DELETE | RAG ingestion & query |
| `/api/v1/approvals` (+`/{id}/resolve`) | GET/POST | Human approval queue |
| `/api/v1/auth/google/*` | GET/POST | Calendar/Gmail OAuth |
| `/api/v1/voice/transcribe`, `/voice/speak` | POST | STT / TTS |

All endpoints except `auth/register`, `auth/login`, and `health` require `Authorization: Bearer <token>`.

## Agent architecture

The supervisor emits a structured `SupervisorDecision` (route list or direct answer). Each specialist receives an agent-scoped system prompt and emits a `ToolPlan`; only tools in its registry allow-list can execute. Tool results are sanitized, summarized back to the supervisor, and the loop repeats within a step budget. Only structured outputs cross agent boundaries — no free-form chain-of-thought is stored or exposed; client-facing traces carry names, statuses, and durations only.

## Tool architecture

Every tool extends `Tool` (or `ApprovalGateTool` for mutations): strict Pydantic argument validation, bounded retries for transient failures (retryable `ToolError`), and automatic trace events. The registry (`app/tools/registry.py`) maps agent → allowed tool classes and rejects unauthorized calls before execution. Approval-gated tools convert any invocation into a `pending_approval` row; execution happens only through the approval resolver after a human decision.

## Security

- **Secrets** — env-only; nothing hardcoded; `.env` gitignored; token/JWT/PBKDF2 values never logged.
- **Auth & authorization** — PBKDF2-HMAC-SHA256 (200k iterations, constant-time compare); HS256 JWT with expiry; generic login errors prevent user enumeration; per-user scoping verified by tests (foreign IDs → 404, listings isolated).
- **CORS** — explicit origin allow-list from settings (no wildcard).
- **SQL injection** — SQLAlchemy ORM with bound parameters throughout; no string-built SQL.
- **Prompt injection** — external content (tool results, document text) is sanitized (`app/core/content_guard.py`) and rendered as untrusted data in prompts; a dedicated test proves injected instructions are neutralized before reaching the LLM.
- **Unauthorized tool execution** — allow-list registry; approval gating for all mutating actions; audit log for approvals and executions.
- **File uploads** — extension + MIME allow-list (PDF/TXT/DOCX), 10 MB cap, per-user ownership on every read/delete.
- **OAuth scopes** — least-privilege: `gmail.readonly`, `gmail.compose`, `gmail.send`, `calendar.events`, `calendar.readonly`; tokens encrypted at rest (Fernet).

## Evaluation

`backend/eval/dataset.json` defines **114 realistic tasks** across calendar, email, weather, research, tasks, multi-step, ambiguous, invalid, and unsafe categories, each with expected agent(s), tool(s), and outcome.

Run the harness:

```bash
cd backend
python -m eval.run_eval                 # deterministic keyword provider (no API key)
python -m eval.run_eval --provider real # configured LLM (needs GEMINI_API_KEY)
python -m eval.run_eval --json report.json
```

Measured: task success rate, behavior-class accuracy, routing accuracy, tool-selection accuracy, failed tool calls, approval-gating correctness, latency (mean/median/p95), per-category breakdown.

**What the numbers mean (and don't):** with the built-in keyword provider the harness validates orchestration mechanics — routing/accuracy figures reflect a simple heuristic, **not** LLM quality. Tool execution is stubbed (same names/schemas; approval tools return the real pre-approval envelope), so latency excludes real LLM/tool network time, and `tool_failure` items *simulate* upstream errors to verify graceful degradation. Meaningful accuracy numbers require `--provider real` with a live key; none are claimed here. Latest keyword-provider run: 114 items, behavior-class accuracy 87.7%, approval gating 104/114, 0 unhandled failures.

## Deployment (Part 9)

**Target architecture:** frontend → static hosting (or nginx container) · backend → Google Cloud Run · database → managed PostgreSQL (Cloud SQL / Neon) · Redis → Memorystore / Upstash · secrets → Google Secret Manager · CI/CD → GitHub Actions.

### What Part 9 added

| Requirement | Where |
|---|---|
| Production env vars | [.env.production.example](.env.production.example) (env + Secret Manager split), production startup guardrail in `app/core/config.py → validate_production()` (called from `create_app()` when `APP_ENV=production`) |
| Secure CORS | explicit origin allow-list from `CORS_ORIGINS`; verified: foreign-origin preflight refused, listed origin allowed |
| HTTPS | terminated by Cloud Run (automatic TLS) / static host; production code path is served over TLS only |
| Database migrations | `alembic upgrade head` run by compose, the deploy workflow (with credentials from repo secrets), or Cloud Run startup |
| Health checks | `GET /api/v1/health` — fast liveness probe (no auth, no DB, no external calls, safe for Cloud Run probes) and `GET /api/v1/health/detail` — bounded DB + migration-revision diagnostics |
| Logging | structured access log (request_id, method, path, status, latency — never bodies/keys) to stdout, collected natively by Cloud Logging |
| Error monitoring | optional Sentry (`SENTRY_DSN` via Secret Manager; `pip install -e "backend[monitoring]"`); enabled lazily only when the DSN is set |
| Cloud Run config | [cloudrun/service.yaml](cloudrun/service.yaml) — startup/liveness probes on `/api/v1/health`, min 0/max 5 instances, 512 MiB, secretKeyRefs, `PORT=8080` |
| Production Docker image | [backend/Dockerfile](backend/Dockerfile) — multi-stage wheels, non-root user, `PORT`-aware healthcheck and uvicorn bind |
| GitHub Actions deployment | [.github/workflows/deploy.yml](.github/workflows/deploy.yml) — lint + backend + frontend tests → Workload-Identity build/push → migrations → Cloud Run deploy → post-deploy health poll; manual dispatch only |

### Production deployment — exact commands

One-time setup (values live in the cloud only, never in git):

```bash
gcloud config set project PROJECT_ID
gcloud services enable run.googleapis.com sqladmin.googleapis.com \
  redis.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com

# Artifact Registry for images
gcloud artifacts repositories create voiceops --repository-format=docker \
  --location=REGION 2>/dev/null || true

# Create Cloud SQL Postgres (pgvector is a plain extension) + Memorystore Redis
gcloud sql instances create voiceops-db --database-version=POSTGRES_16 \
  --tier=db-custom-1-3840 --region=REGION
gcloud sql databases create voiceops --instance=voiceops-db
gcloud sql users create voiceops --instance=voiceops-db --password=...   # secret-managed
gcloud redis instances create voiceops-redis --size=1 --region=REGION

# Secrets (interactive; skips existing). Requires GEMINI_API_KEY etc.
./scripts/create_secrets.sh

# Register the Cloud Run service schema (probes + secretKeyRefs)
gcloud run services replace cloudrun/service.yaml --region=REGION
```

Then deploy either via **GitHub Actions** (`Actions → Deploy → Run workflow`) or locally:

```bash
gcloud run deploy voiceops-backend --region=REGION \
  --source ./backend   # Cloud Build (or use the workflow-built Artifact Registry image)
gcloud run services update-traffic voiceops-backend --to-latest --region=REGION

# Frontend: copy the static site to any static host and set js/config.js
gsutil -m rsync -r frontend gs://voiceops-frontend
# then set js/config.js: window.VOICEOPS_API_URL = "https://your-backend.run.app" (backend URL) and re-upload
```

### Post-deployment verification (12 checks)

Run against the live URLs; do not take these on faith:

1. **Frontend loads** — open the frontend URL; page renders, no console errors.
2. **Backend responds** — `curl https://backend-url/api/v1/health` → `{"status":"ok","service":"voiceops"}`; `/api/v1/health/detail` shows `database.reachable=true` and a migration revision.
3. **Login works** — register + login from Settings → Account; token stored; subsequent calls carry `Authorization: Bearer …`.
4. **Chat works** — send a message; response + trace returned; a rate-limit burst behaves (429 with `Retry-After`).
5. **Voice works** — speak → transcript + audio reply.
6. **Agent routing works** — "What's the weather in Berlin?" routes to weather agent and returns real data.
7. **Calendar works** — connect Google, list events.
8. **Gmail approval works** — ask to draft an email; approve in the panel; verify send + audit row.
9. **Tasks work** — create/complete tasks through chat or panel.
10. **Database persists data** — reload page; conversations/tasks/approvals remain; survives a new Cloud Run instance (cold start does not lose data).
11. **Agent activity visible** — Activity panel shows agent runs and tool traces.
12. **Errors handled correctly** — send an invalid message: structured JSON error envelope with code and `X-Request-ID`; check Cloud Logging for the same request id, no secrets in log lines.

## LIVE DEMO / GITHUB / DEMO VIDEO

No deployment has been executed yet, so no public URLs exist to publish.

- LIVE DEMO: _pending first deployment — will be the frontend static-hosting URL; do not fill in a placeholder URL._
- GITHUB: _pending — add the repo URL after the repository is pushed._
- DEMO VIDEO: _pending — record after deployment._

## Demo

1. Open http://127.0.0.1:3000, register, sign in.
2. Chat: "What's the weather in Berlin?" → weather agent → real Open-Meteo call.
3. "Email sam@example.com the report" → approval appears in the Approvals panel → approve → Gmail send executes (audit-logged).
4. Upload a PDF → "What technologies are listed in my resume?" → grounded answer with `Source: resume.pdf / Page N`.
5. Click the mic and speak — the answer comes back as audio.

## Roadmap

1. Part 1 — Foundation ✅
2. Part 2 — LLM provider layer (Gemini) ✅
3. Part 3 — LangGraph orchestration + tool calling ✅
4. Part 4 — PostgreSQL persistence + memory ✅
5. Part 5 — Voice (Deepgram STT, ElevenLabs TTS) ✅
6. Part 6 — Google OAuth + human-in-the-loop approvals ✅
7. Part 7 — Knowledge Agent + RAG (pgvector) ✅
8. Part 8 — Production engineering, security, observability, evaluation ✅
9. Next: real deployment, distributed rate limiting, pgvector SQL-side ranking, Redis-backed limits
