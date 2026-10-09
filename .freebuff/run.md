# VoiceOps — local run doc (this workspace = the main checkout)

Two processes serve the live preview. Both must be running; see Start below.

## Reproduce artifacts (fresh checkout)

1. Python 3.12 + venv at `.venv/` with `pip install -e "backend[dev]" pydantic[email] aiosqlite`.
2. `backend/.env` — copy the structure from `backend/.env` (do NOT copy from git history;
   it is gitignored). Values used for this preview (no secrets):
   - `LLM_PROVIDER=keyword` — deterministic no-key demo fallback
   - `DATABASE_URL_OVERRIDE=sqlite+aiosqlite:///./preview.db` — local SQLite (no Postgres on this machine)
   - `PORT=8000`, `APP_ENV=development`, `LOG_LEVEL=info`
   Note: `.env` values except the two above are placeholders; real keys go in Secret Manager in production.
3. Create the SQLite schema once (backend cwd):
   `python -c "import asyncio; from sqlalchemy.ext.asyncio import create_async_engine; from app.models import Base; (lambda: ...)()"` — see Start step 2 for the exact command.
4. Demo login already exists: `preview@voiceops.dev` / `PreviewPass1!` (created via /auth/register).
   To reset: delete `backend/preview.db`, re-run step 3, re-register from the UI.

## Start servers

1. Backend (port 8000, detached, logs to `.freebuff/preview-<threadId>.log[.err]`):
   ```
   powershell -NoProfile -Command "(Start-Process -FilePath 'E:\VoiceOps_project\.venv\Scripts\python.exe' -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000' -WorkingDirectory 'E:\VoiceOps_project\backend' -RedirectStandardOutput 'E:\VoiceOps_project\.freebuff\preview-<threadId>.log' -RedirectStandardError 'E:\VoiceOps_project\.freebuff\preview-<threadId>.log.err' -WindowStyle Hidden -PassThru).Id"
   ```
   Health: `curl http://127.0.0.1:8000/api/v1/health` → `{"status":"ok"}`.
2. Frontend (port 3000, static http.server, detached):
   ```
   powershell -NoProfile -Command "(Start-Process -FilePath 'E:\VoiceOps_project\.venv\Scripts\python.exe' -ArgumentList '-m','http.server','3000','--bind','127.0.0.1' -WorkingDirectory 'E:\VoiceOps_project\frontend' -RedirectStandardOutput '<log>' -RedirectStandardError '<log>.err' -WindowStyle Hidden -PassThru).Id"
   ```
3. URL: `http://127.0.0.1:3000/` (frontend) → talks to `http://127.0.0.1:8000` (backend).
   The browser keeps `voiceops_api_url=http://127.0.0.1:8000` in localStorage (or configure
   `frontend/js/config.js`, left empty so same-origin/HTTPS deployments need no edit).

## Tests

- Backend: `cd backend && ../.venv/Scripts/python.exe -m pytest -q` (81 passed at last run)
- Lint: `ruff check app eval tests`
- Frontend: `node --test frontend/tests/api.test.mjs`

## Known limitations of this dev preview

- SQLite instead of Postgres/pgvector → RAG document upload/search needs embeddings
  (Gemini key) and pgvector; those features are NOT exercisable in this preview.
- `LLM_PROVIDER=keyword` is a keyword heuristic demo router, not an LLM — chat replies
  are canned phrasings; routing/tools run for real.
- Voice (Deepgram/ElevenLabs) requires their API keys; without keys the endpoints
  return 503 envelopes — graceful by design.
