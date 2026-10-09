/**
 * Central API client for the VoiceOps frontend.
 *
 * Keep every backend call in this file so later parts (auth headers,
 * websockets, error toasts) only need to change here.
 */

// Backend origin, resolved in priority order:
//   1. ?api=… query param  ( handy for testing a specific backend)
//   2. localStorage override (quick local testing)
//   3. window.VOICEOPS_API_URL injected via js/config.js (production / static hosting)
//   4. same-origin (works when nginx/CDN proxies /api/v1)  → empty base URL
//   5. local dev default: http://127.0.0.1:8000
function _apiBaseUrl() {
  const sameOriginOk =
    typeof window !== "undefined" &&
    (location.protocol === "https:" || location.hostname === "localhost" || location.hostname === "127.0.0.1");

  const param = new URLSearchParams(location.search).get("api");
  if (param) return param.replace(/\/+$/, "");

  const override = localStorage.getItem("voiceops_api_url");
  if (override) return override.replace(/\/+$/, "");

  if (typeof window !== "undefined" && window.VOICEOPS_API_URL) {
    return window.VOICEOPS_API_URL.replace(/\/+$/, "");
  }

  // In production over HTTPS we can only hit same-origin (mixed content).
  // An empty base URL means requests go to the serving origin itself —
  // which is correct when nginx proxies /api/v1 or a CDN handles /api/*.
  if (sameOriginOk || location.protocol === "https:") return "";

  return "http://127.0.0.1:8000";
}

const API_BASE_URL = _apiBaseUrl();

// ---------- Auth (Part 8) ----------

function getAuthToken() {
  return localStorage.getItem("voiceops_token") || null;
}

function setAuthToken(token) {
  if (token) localStorage.setItem("voiceops_token", token);
  else localStorage.removeItem("voiceops_token");
}

function authHeaders() {
  const token = getAuthToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function handleAuthFailure() {
  // Expired/invalid token: drop it so the login panel reappears.
  setAuthToken(null);
  if (typeof showLoginPanel === "function") showLoginPanel();
}

/**
 * Perform a JSON request against the backend.
 * Throws an Error with a readable message when the response is not ok.
 */
async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...authHeaders() },
    ...options,
  });

  let body = null;
  try {
    body = await response.json();
  } catch {
    // Non-JSON body: fall through with body = null
  }

  if (!response.ok) {
    if (response.status === 401) handleAuthFailure();
    const message =
      body && body.error && body.error.message
        ? body.error.message
        : `Request failed with status ${response.status}`;
    throw new Error(message);
  }

  return body;
}

async function registerUser(email, name, password) {
  const body = await apiRequest("/api/v1/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, name, password }),
  });
  setAuthToken(body.access_token);
  return body;
}

async function loginUser(email, password) {
  const body = await apiRequest("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  setAuthToken(body.access_token);
  return body;
}

function logoutUser() {
  setAuthToken(null);
}

/** Fetch health status from the backend. */
async function fetchHealth() {
  return apiRequest("/api/v1/health");
}

// ---------- Data endpoints (Part 4) ----------

async function fetchConversations() {
  return apiRequest("/api/v1/conversations");
}

async function fetchConversationDetail(id) {
  return apiRequest(`/api/v1/conversations/${id}`);
}

async function fetchTasks(status) {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiRequest(`/api/v1/tasks${query}`);
}

async function createTask(title) {
  return apiRequest("/api/v1/tasks", {
    method: "POST",
    body: JSON.stringify({ title }),
  });
}

async function completeTask(id) {
  return apiRequest(`/api/v1/tasks/${id}/complete`, { method: "POST" });
}

async function fetchActivity(limit = 20) {
  return apiRequest(`/api/v1/activity?limit=${limit}`);
}

// ---------- Voice endpoints (Part 5) ----------

/**
 * Upload recorded audio for transcription. Returns {text, provider, confidence}.
 * Error responses carry our standard error envelope.
 */
async function transcribeAudio(audioBlob) {
  const formData = new FormData();
  formData.append("audio", audioBlob, "recording.webm");
  if (sessionId) formData.append("session_id", sessionId);

  const response = await fetch(`${API_BASE_URL}/api/v1/voice/transcribe`, {
    method: "POST",
    headers: authHeaders(), // multipart: browser sets the boundary itself
    body: formData,
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401) handleAuthFailure();
    throw new Error(body && body.error ? body.error.message : `Transcription failed (${response.status})`);
  }
  return body;
}

/** Request spoken audio for a text reply. Returns a Blob of audio/mpeg. */
async function synthesizeSpeech(text) {
  const response = await fetch(`${API_BASE_URL}/api/v1/voice/speak`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ text }),
  });
  if (!response.ok) {
    if (response.status === 401) handleAuthFailure();
    const body = await response.json().catch(() => null);
    throw new Error(body && body.error ? body.error.message : `Speech failed (${response.status})`);
  }
  return response.blob();
}

// ---------- Google OAuth + Approvals (Part 6) ----------

async function fetchGoogleStatus() {
  return apiRequest("/api/v1/auth/google/status");
}

async function disconnectGoogle() {
  return apiRequest("/api/v1/auth/google/disconnect", { method: "POST" });
}

async function fetchApprovals(status) {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiRequest(`/api/v1/approvals${query}`);
}

async function resolveApproval(id, decision, confirmed, editedPayload) {
  return apiRequest(`/api/v1/approvals/${id}/resolve`, {
    method: "POST",
    body: JSON.stringify({
      decision,
      confirmed: !!confirmed,
      edited_payload: editedPayload || null,
    }),
  });
}

async function fetchAudit(limit = 100) {
  return apiRequest(`/api/v1/audit?limit=${limit}`);
}

// ---------- Documents (Part 7) ----------

async function fetchDocuments() {
  return apiRequest("/api/v1/documents");
}

async function uploadDocument(file) {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(`${API_BASE_URL}/api/v1/documents`, {
    method: "POST",
    headers: authHeaders(), // multipart: browser sets the boundary itself
    body: formData,
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401) handleAuthFailure();
    throw new Error(body && body.error ? body.error.message : `Upload failed (${response.status})`);
  }
  return body;
}

async function deleteDocument(id) {
  return apiRequest(`/api/v1/documents/${id}`, { method: "DELETE" });
}

async function searchDocuments(query) {
  return apiRequest(`/api/v1/documents/search?q=${encodeURIComponent(query)}`);
}
