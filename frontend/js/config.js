/**
 * Production configuration (Part 9).
 *
 * Static hosting cannot read server env vars, so the backend origin is
 * injected here. After frontend files are copied into the hosting
 * bucket/directory, edit the URL below to the live Cloud Run endpoint,
 * e.g. window.VOICEOPS_API_URL = "https://voiceops-backend-xxxx.a.run.app";
 *
 * In the default Docker deployment (nginx proxying /api/v1) this stays
 * empty and requests go to the same origin — no CORS needed.
 *
 * This file is gitignored ONLY in the form containing a real URL is not
 * recommended either — the backend URL is not secret (it is public),
 * so committing the production value here is fine and convenient.
 */
window.VOICEOPS_API_URL = "";
