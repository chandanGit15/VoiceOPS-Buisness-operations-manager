/**
 * Authentication UI (Part 8).
 *
 * Wires the Settings → Account panel to the auth endpoints in api.js.
 * All dashboard data requires a bearer token: when none is stored, a
 * notice is shown and panels load empty; after sign-in the user reloads
 * into a fully authorized session.
 */

function authMessage(text) {
  const el = document.getElementById("auth-message");
  if (el) el.textContent = text;
}

// Periodic sign-in reminder on the chat panel while signed out.
function signedOutNotice() {
  const el = document.getElementById("chat-status");
  if (el && !getAuthToken()) {
    el.textContent = "Sign in (Settings → Account) to chat — all data is per-user.";
    el.classList.add("chat-status--error");
  }
}

function showLoginPanel() {
  const forms = document.getElementById("auth-forms");
  const logoutBtn = document.getElementById("auth-logout");
  if (forms) forms.style.display = "";
  if (logoutBtn) logoutBtn.style.display = "none";
}

function showSignedIn(name, email) {
  const forms = document.getElementById("auth-forms");
  const logoutBtn = document.getElementById("auth-logout");
  if (forms) forms.style.display = "none";
  if (logoutBtn) logoutBtn.style.display = "";
  authMessage(`Signed in as ${name || email}`);
}

function requireAuthNotice() {
  if (!getAuthToken()) {
    authMessage("Sign in to use the assistant (all data is per-user).");
    return false;
  }
  return true;
}

// Guard the chat form: without a token the backend would 401 every turn.
document.addEventListener("submit", (event) => {
  const form = event.target;
  if (form && form.id === "chat-form" && !getAuthToken()) {
    event.preventDefault();
    event.stopPropagation();
    signedOutNotice();
  }
}, true);

document.getElementById("auth-login")?.addEventListener("click", async () => {
  const email = document.getElementById("auth-email").value.trim();
  const password = document.getElementById("auth-password").value;
  if (!email || !password) { authMessage("Email and password required."); return; }
  try {
    const body = await loginUser(email, password);
    showSignedIn(body.user.name, body.user.email);
    sessionId = null; // conversations are per-user: start fresh
    localStorage.removeItem("voiceops_session_id");
    checkHealth();
  } catch (err) {
    authMessage(err.message);
  }
});

document.getElementById("auth-register")?.addEventListener("click", async () => {
  const email = document.getElementById("auth-email").value.trim();
  const name = document.getElementById("auth-name").value.trim();
  const password = document.getElementById("auth-password").value;
  if (!email || !name || password.length < 8) {
    authMessage("Email, name and an 8+ character password are required.");
    return;
  }
  try {
    const body = await registerUser(email, name, password);
    showSignedIn(body.user.name, body.user.email);
  } catch (err) {
    authMessage(err.message);
  }
});

document.getElementById("auth-logout")?.addEventListener("click", () => {
  logoutUser();
  sessionId = null;
  localStorage.removeItem("voiceops_session_id");
  showLoginPanel();
  authMessage("Signed out.");
});

// Restore session state on load.
if (getAuthToken()) showSignedIn("", "saved session");
else {
  showLoginPanel();
  signedOutNotice();
}
