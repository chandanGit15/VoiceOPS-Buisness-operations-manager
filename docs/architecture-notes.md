# Architecture notes (living document)

Updated each part so reviewers can follow the reasoning without reading all code.

## Part 1 — Foundation decisions

- **App factory (`create_app()`)** instead of a module-level app: makes tests create isolated instances and later lets us build variants (e.g., different settings for Cloud Run).
- **Versioned prefix `/api/v1`**: breaking API changes later go to `/api/v2` without breaking the voice frontend mid-development.
- **`APIError` hierarchy + registered handlers**: business code raises typed exceptions; the response envelope `{"status": "error", "error": {...}}` is uniform. Unexpected exceptions are logged server-side but return a generic message — no stack traces to clients.
- **Settings via pydantic-settings**: env is the only source of truth; Postgres/Redis vars are declared now so later parts change no config plumbing.
- **`frontend/js/api.js` as the single network boundary**: when auth and WebSockets arrive, only this file changes.
- **No TypeScript, no framework**: dashboard is one HTML shell, one CSS file, two small JS files — matching the skill constraint and keeping maintenance cheap.
- **Docker Compose only runs infra**: the backend runs locally with hot reload during development; backend containerization is added in the deployment part.

## Part 2 — LLM service and first agent

- **Provider boundary**: agents see only `LLMProvider` (abstract) + `LLMMessage`/`LLMResponse` DTOs. `get_llm_provider()` reads `LLM_PROVIDER` and instantiates the vendor adapter lazily, so the app boots even without a vendor SDK installed. Adding a second provider later = one new adapter class + one line in the factory.
- **`google-genai` SDK** (official), async client (`client.aio.models.generate_content`). All SDK exceptions are narrowed to `LLMError` (502 envelope) so vendor details never leak to clients; usage metadata is captured for future observability.
- **SupervisorAgent** takes an injectable provider — tests pass `FakeProvider`, production resolves via the factory. `handle(history, message)` is the stable interface Part 3's LangGraph entry node will keep.
- **Conversation store** is in-process with TTL (1h) and a bounded context window (40 turns). The interface (get/append/clear) matches what a Redis store needs, so the swap is a drop-in.
- **History is written only after a successful generation** — a failed request never pollutes session context.
- **API key handling**: `GEMINI_API_KEY` is read once into server settings, passed to the SDK constructor, and never appears in any response payload, log line, or frontend file. The frontend only knows `API_BASE_URL` (localhost:8000).
- **Request flow**:
  Browser `POST /api/v1/chat` → Pydantic validation → router loads session history from `ConversationStore` → `SupervisorAgent.handle()` → `LLMProvider.generate()` → Gemini → normalized `LLMResponse` → both turns appended to store → structured `ChatResponse`.

## Part 3 — Multi-agent orchestration and tool calling

- **Graph** (LangGraph `StateGraph`): `supervisor → {research | task | weather} → supervisor → … → END`. The supervisor re-routes after every specialist, using their summaries as context — this is what makes multi-step requests ("check rain tomorrow, then remind me") work. `SUPERVISOR_MAX_STEPS` (default 6) is a hard loop guard that forces a direct answer.
- **Routing & planning are structured outputs** (`SupervisorDecision`, `ToolPlan` Pydantic models via Gemini's native JSON mode). No reasoning field exists in any schema, so no chain-of-thought can leak — the API exposes only `TraceEvent`s (agent, action, tool, status, timestamp, duration_ms, error).
- **Security**: the tool registry (`tools/registry.py`) maps every agent to an explicit tool whitelist; a specialist asking for another agent's tool gets a trace error, never an execution. Tool arguments are validated by Pydantic schemas before execution; the LLM never passes free-form payloads into business logic.
- **Real tools, no simulation**: `get_weather` calls Open-Meteo (geocoding + forecast, no key), `search_web` calls Tavily (`TAVILY_API_KEY`), task tools mutate a real `TaskStore` whose interface matches the future Postgres store.
- **Retries**: `Tool._run` retries retryable errors (network, 429/5xx) with linear backoff (`TOOL_MAX_RETRIES`, `TOOL_RETRY_DELAY_SECONDS`); business errors (unknown city, task not found, missing key) fail fast and are recorded in the trace so the agent can report them gracefully. `with_llm_retries` does the same for LLM calls; a supervisor-decision LLM failure propagates as a 502 envelope.
- **DI for tests**: HTTP clients come from `_client()` factories (monkeypatched with `httpx.MockTransport`), tool classes are injectable per-agent into `build_graph`, and the LLM is a scripted `FakeProvider`. Tests cover: routing, direct answers, multi-agent execution order, unauthorized-tool rejection, step budget, retry-then-fail, and task roundtrips.

## Part 4 — PostgreSQL, conversation state and memory

- **Stack**: SQLAlchemy 2.0 async ORM (asyncpg engine, one per process via `database/session.py`), Alembic for migrations. `alembic.ini` + `migrations/env.py` inject the URL from environment settings; the initial migration (`0001_part4`) creates all seven tables and is hand-written, so `alembic upgrade head` works against any fresh Postgres.
- **Schema**: users, conversations, messages, agent_runs, tool_calls, tasks, memories — UUID PKs, timezone-aware timestamps, FK cascades (messages/runs/tool_calls die with their conversation), and composite index on `tasks(user_id, status)`.
- **No pgvector yet**: no RAG at this stage; `memories.metadata` is JSONB so a vector column can be added later without a reshape.
- **Conversation persistence**: `POST /api/v1/chat` resolves/creates the conversation row (session_id = conversation UUID), loads the last 30 messages as short-term memory, and appends both turns only after a successful run.
- **Agent execution history**: every chat turn records an `agent_runs` row plus one `tool_calls` row per trace event (tool name, status, duration/error in `output_data`). Prompts and raw payloads are still never stored.
- **Long-term memory**: `memories` rows (fact/preference/note) are injected into the supervisor's context block each turn; managed via `POST/GET /api/v1/memories`.
- **Task store**: the in-memory store is replaced by DB rows; task tools open their own session from `db_session.get_session_factory` (kept a module-level function so tests can patch it).
- **Endpoints** (`app/api/data.py`): conversations list/detail with messages, tasks CRUD (`GET/POST /api/v1/tasks`, `POST /api/v1/tasks/{id}/complete`), activity (`GET /api/v1/activity` → runs + tool calls), memories.
- **Testing strategy**: the suite runs on SQLite (aiosqlite) with `Base.metadata.create_all` — same models, no Docker needed; Alembic owns the real-Postgres schema. Fixtures in `tests/conftest.py`: `db_engine`, `db_session_factory`, `patched_session_factory` (for tools), `patched_db` (overrides the request-scoped `get_session` dependency). Offline checks (`alembic upgrade head --sql`, `alembic downgrade 0001_part4:base --sql`) validate the full migration chain without a live DB.
- **Seed script**: `backend/scripts/seed_db.py` bootstraps the dev user, a welcome conversation, sample tasks, and memories.
- **Single-user mode**: all rows belong to a bootstrapped `dev@voiceops.local` user; per-user auth and ownership scoping arrive with OAuth in a later part.

## Part 5 — Real-time voice

- **Provider abstractions**: `STTProvider` / `TTSProvider` ABCs with DTO results (`TranscriptionResult`, `SpeechResult`), factory-selected by `STT_PROVIDER` / `TTS_PROVIDER` env vars — mirrors the LLM layer, so swapping vendors is one adapter + one factory line.
- **Deepgram STT** (nova-2, pre-recorded listen API) and **ElevenLabs TTS** (multilingual v2, mp3) are the first adapters. Keys are read once into server settings and never appear in any response, log, or frontend file; the frontend only ever talks to `/api/v1/voice/*`.
- **Audio handling**: uploads are capped at 15 MB, content-type checked (audio/* only), streamed straight through to the vendor and never persisted — raw audio is not stored anywhere.
- **Endpoints**: `POST /api/v1/voice/transcribe` (multipart upload → `{text, provider, confidence, session_id}`) and `POST /api/v1/voice/speak` (`{text}` → `audio/mpeg` bytes). Retryable vendor errors (network, 429/5xx) retry with backoff via `_vendor_call_with_retry`; business errors fail fast.
- **Architecture choice (streaming)**: implemented the reliable request/response pipeline first, per the brief. The pipeline is deliberately modular (record → transcribe → chat → speak), so a WebSocket streaming upgrade can replace individual links without touching the agent system.
- **Same agent system**: the frontend sends the transcript to the existing `/api/v1/chat` — there is no separate voice chatbot, and voice turns land in the same conversations, agent_runs, and tool_calls tables.
- **Frontend state machine** (`js/voice.js`): IDLE → LISTENING → PROCESSING → SPEAKING → IDLE, with an ERROR state + retry button. Mic permission handling, start/stop, playback, and error surfaces are explicit. TTS failure degrades gracefully: the text reply is still shown (spoken output is an enhancement, never a single point of failure). Live/partial transcript is a best-effort browser Web Speech API enhancement; the Deepgram transcript is always authoritative.

## Part 6 — Google Calendar, Gmail, and human-in-the-loop approvals

- **OAuth 2.0**: `services/google_oauth.py` implements the full code-exchange flow (`/auth/google/login` → Google consent → `/auth/google/callback`), with `state` CSRF protection, `access_type=offline` + `prompt=consent` to always obtain a refresh token. Least-privilege scopes: `gmail.readonly`, `gmail.compose`, `gmail.send`, `calendar.events`, `calendar.readonly` — nothing that could delete mail or manage calendar ACLs.
- **Tokens at rest**: Fernet-encrypted (TOKEN_ENCRYPTION_KEY env var) in `google_oauth_tokens`. Decryption happens only in memory; a wrong/rotated key fails closed (re-auth required). Refresh happens automatically in `get_valid_access_token` with a 60s expiry margin; consent revoked → clear "reconnect Google" error.
- **Google clients**: `services/google_clients.py` wraps Calendar v3 + Gmail v1 REST via httpx (no vendor SDK); 401/403 → actionable re-auth error, 429/5xx → retryable, everything else → explicit GoogleAPIError. `GoogleContext` bundles the DB session + user per request.
- **Approval gating**: `ApprovalGateTool.run()` NEVER executes — it records a pending `approvals` row (tool, validated payload, risk_level, human-readable summary) and returns `awaiting_approval`. Read-only tools (get_events, check_availability, search_emails, read_email, draft_email) run immediately. Only `approvals.resolve_approval()` executes real actions — via `ctx.direct_execution` (the gate-bypass flag that tests prove can't be triggered accidentally). High-risk actions (send_email, delete_event) additionally require `confirmed: true`.
- **Risk levels**: send_email/delete_event = high; create_event/update_event = medium; draft_email = low (a draft never leaves the account).
- **Audit trail**: append-only `audit_log` (approval.created/approved/rejected/executed/execution_failed + auth events), queryable at `GET /api/v1/audit`; approvals themselves keep created_at/resolved_at.
- **Graph**: supervisor routes to 5 agents now (research, task, weather, calendar, email); `build_graph` injects the tool context (DB session + user) so Google tools resolve OAuth tokens and write approval rows mid-conversation.
- **Migration 0002** adds `google_oauth_tokens`, `approvals`, `audit_log`; validated offline in both directions.
- **Failure paths**: not connected → 409 `google_not_connected`; OAuth misconfigured → 400 `oauth_not_configured`; token decrypt failure → fail-closed re-auth. All proven by tests.

## Part 7 — Personal Knowledge Agent and RAG

- **Pipeline**: upload (multipart, ≤ UPLOAD_MAX_MB) → extraction (pypdf / python-docx / plain text) → page-aware chunking (CHUNK_SIZE_CHARS with CHUNK_OVERLAP_CHARS overlap; chunks never cross pages so citations stay precise) → embeddings → `documents` + `document_chunks` tables (pgvector).
- **Embedding provider layer**: same abstraction pattern as LLM/STT/TTS — `EmbeddingProvider` ABC + factory, Gemini `text-embedding-004` first adapter, selected by `EMBEDDING_PROVIDER`.
- **Dialect portability**: the embedding column is `vector(768)` on Postgres via a `with_variant(JSON())` fallback for SQLite. Retrieval ranks by cosine similarity in Python today (works everywhere, fine at personal-corpus scale); the pgvector `<=>` SQL path is the noted scaling optimization.
- **Grounding enforcement (the important part)**: `answer_from_documents` refuses to even call the LLM when no chunk clears `RETRIEVAL_MIN_SIMILARITY` — the "irrelevant query" test asserts the LLM was invoked **zero times**. When it does run, the prompt restricts the model to the retrieved passages, mandates the `Source:\n<filename>\nPage <n>` citation format, and forbids general knowledge. The Knowledge Agent's prompts repeat the rule (explicit document questions route to `knowledge`, never answered from model memory).
- **Graph**: supervisor routes to 6 agents now (…, knowledge). The chat response carries the grounded answer + citations; voice queries flow through the same pipeline (TTS reads the answer; citation lines are short by design).
- **APIs**: `POST/GET/DELETE /api/v1/documents`, `GET /api/v1/documents/search?q=` (semantic search returning cited excerpts). Documents are per-user (join-scoped), deletion cascades chunks.
- **Migration 0003**: `CREATE EXTENSION IF NOT EXISTS vector`, documents + document_chunks; validated offline.
