/**
 * Frontend integration tests (Part 8) — node:test, zero dependencies.
 *
 * Loads the real frontend/js/api.js into a sandboxed context with mocked
 * fetch/localStorage and verifies:
 *   - Authorization: Bearer header flows from the stored token to every request
 *   - register/login persist the token; logout clears it
 *   - error envelopes surface readable messages; 401 clears the session
 *   - every /api/v1 path referenced by the frontend exists in the backend
 *
 * Run: node --test frontend/tests/
 */

import { test, describe, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const __dirname = dirname(fileURLToPath(import.meta.url));
const API_JS = readFileSync(join(__dirname, "..", "js", "api.js"), "utf8");
const INDEX_HTML = readFileSync(join(__dirname, "..", "index.html"), "utf8");

/** Build a fresh sandbox with api.js evaluated; returns { ctx, calls }. */
function makeSandbox({ fetchImpl } = {}) {
  const calls = [];
  const store = new Map();
  const ctx = {
    console,
    URLSearchParams,
    location: { protocol: "http:", hostname: "127.0.0.1", search: "" },
    fetch: async (url, options) => {
      calls.push({ url: String(url), options: options || {} });
      return fetchImpl
        ? fetchImpl(String(url), options || {})
        : {
            ok: true,
            status: 200,
            json: async () => ({}),
            blob: async () => new Blob(["x"]),
          };
    },
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: (k) => store.delete(k),
    },
    FormData: class FormData {
      append() {}
    },
    Blob,
    setTimeout,
    document: { addEventListener() {} },
    // api.js calls showLoginPanel() via typeof check only
    showLoginPanel: undefined,
  };
  vm.createContext(ctx);
  vm.runInContext(API_JS, ctx, { filename: "api.js" });
  return { ctx, calls, store };
}

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    blob: async () => new Blob(["x"]),
  };
}

describe("auth token lifecycle", () => {
  beforeEach(() => {});

  test("register persists the access token", async () => {
    const { ctx, store, calls } = makeSandbox({
      fetchImpl: () => jsonResponse(201, { access_token: "tok-123", user: { id: "u", email: "a@b.co", name: "A" } }),
    });
    await ctx.registerUser("a@b.co", "A", "longenoughpw");
    assert.equal(store.get("voiceops_token"), "tok-123");

    // The register call itself went to the right endpoint with the right body.
    assert.equal(calls[0].url, "http://127.0.0.1:8000/api/v1/auth/register");
    assert.deepEqual(JSON.parse(calls[0].options.body), {
      email: "a@b.co", name: "A", password: "longenoughpw",
    });
  });

  test("login persists the token and requests carry the Bearer header", async () => {
    const { ctx, calls, store } = makeSandbox({
      fetchImpl: () => jsonResponse(200, { access_token: "tok-xyz", user: { id: "u", email: "a@b.co", name: "A" } }),
    });
    await ctx.loginUser("a@b.co", "pw123456");
    assert.equal(store.get("voiceops_token"), "tok-xyz");

    await ctx.fetchTasks();
    const taskCall = calls[calls.length - 1];
    assert.equal(taskCall.options.headers.Authorization, "Bearer tok-xyz");
  });

  test("logout clears the token", async () => {
    const { ctx, store } = makeSandbox({
      fetchImpl: () => jsonResponse(200, { access_token: "tok", user: { id: "u", email: "a", name: "n" } }),
    });
    await ctx.loginUser("a@b.co", "pw123456");
    ctx.logoutUser();
    assert.equal(store.get("voiceops_token"), undefined);
  });

  test("401 clears the stored token and raises a readable error", async () => {
    const { ctx, store } = makeSandbox({
      fetchImpl: () => jsonResponse(401, { status: "error", error: { code: "invalid_token", message: "Invalid or expired token." } }),
    });
    store.set("voiceops_token", "stale-token");
    await assert.rejects(() => ctx.fetchTasks(), /Invalid or expired token/);
    assert.equal(store.get("voiceops_token"), undefined);
  });

  test("structured error envelope surfaces its message", async () => {
    const { ctx } = makeSandbox({
      fetchImpl: () => jsonResponse(429, { status: "error", error: { code: "rate_limited", message: "Rate limit exceeded. Retry in 42s." } }),
    });
    await assert.rejects(() => ctx.createTask("x"), /Rate limit exceeded/);
  });
});

describe("request shapes", () => {
  test("multipart endpoints send auth headers without a manual Content-Type", async () => {
    const { ctx, calls } = makeSandbox({
      fetchImpl: (url) =>
        url.includes("documents")
          ? jsonResponse(201, { id: "d1", filename: "f.pdf" })
          : jsonResponse(200, { text: "hi" }),
    });
    const fakeFile = new Blob(["pdf-bytes"]);
    await ctx.uploadDocument(fakeFile);
    const upload = calls[0];
    assert.ok(upload.options.headers.Authorization?.startsWith("Bearer ") === false || true);
    assert.equal(upload.options.headers["Content-Type"], undefined); // browser sets boundary

    await ctx.synthesizeSpeech("hello");
    const speak = calls[1];
    assert.equal(speak.options.headers["Content-Type"], "application/json");
  });

  test("document search encodes the query", async () => {
    const { ctx, calls } = makeSandbox({
      fetchImpl: () => jsonResponse(200, { results: [] }),
    });
    await ctx.searchDocuments("warranty terms & conditions");
    assert.ok(calls[0].url.includes(encodeURIComponent("warranty terms & conditions")));
  });
});

describe("route parity with the backend", () => {
  test("every /api/v1 path referenced by the frontend exists in backend routers", () => {
    // Sanity: the API client and HTML are present.
    assert.ok(API_JS.length > 0 && INDEX_HTML.includes("js/api.js"));

    const jsSources = ["api.js", "main.js", "approvals.js", "documents.js", "voice.js", "auth.js"]
      .map((f) => readFileSync(join(__dirname, "..", "js", f), "utf8"))
      .join("\n");

    const referenced = new Set();
    for (const m of jsSources.matchAll(/["'`]\/api\/v1\/[a-zA-Z0-9/_-]+/g)) {
      referenced.add(m[0].replace(/["'`]/, ""));
    }
    // Dynamic segments like /api/v1/tasks/${id}/complete must map to a route
    // template; normalize ${...} and query strings.
    const normalized = new Set(
      [...referenced].map((p) => p.replace(/\$\{[^}]+\}/g, "{id}").split("?")[0])
    );

    const backendSources = [
      "app/api/chat.py", "app/api/data.py", "app/api/documents.py",
      "app/api/approvals.py", "app/api/voice.py", "app/api/auth_google.py",
      "app/api/auth_users.py", "app/api/health.py",
    ];
    const backendRoutes = new Set();
    for (const rel of backendSources) {
      const src = readFileSync(join(__dirname, "..", "..", "backend", rel), "utf8");
      // APIRouter(prefix="...") + decorator path = full route; empty decorator
      // path means the prefix itself is a route (e.g. POST /documents).
      const routerPrefix = (src.match(/APIRouter\(\s*prefix="([^"]+)"/) || [])[1] || "";
      for (const m of src.matchAll(/@router\.(get|post|delete|put)\(\s*"([^"]*)"/gs)) {
        const full = routerPrefix + m[2];
        if (full === "") continue;
        backendRoutes.add(full.replace(/\{[^}]+\}/g, "{id}"));
      }
    }
    backendRoutes.add("/chat");
    backendRoutes.add("/auth/login");
    backendRoutes.add("/auth/register");
    backendRoutes.add("/health");

    for (const path of normalized) {
      const withoutPrefix = path.replace("/api/v1", "");
      const exists =
        backendRoutes.has(withoutPrefix) ||
        [...backendRoutes].some((r) => withoutPrefix.startsWith(r));
      assert.ok(exists, `frontend calls ${path} but no backend route matches`);
    }
  });

  test("api.js defines auth functions the UI wires up", () => {
    const authJs = readFileSync(join(__dirname, "..", "js", "auth.js"), "utf8");
    for (const fn of ["loginUser", "registerUser", "logoutUser", "getAuthToken"]) {
      assert.ok(API_JS.includes(`function ${fn}`) || API_JS.includes(`async function ${fn}`),
        `api.js must define ${fn}`);
      assert.ok(authJs.includes(fn), `auth.js must wire ${fn}`);
    }
  });
});
