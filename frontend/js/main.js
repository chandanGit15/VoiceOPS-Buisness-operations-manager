/**
 * Frontend entry point.
 * Handles sidebar navigation, backend health polling, and the chat UI.
 */

// ---------- Sidebar navigation ----------

const navItems = document.querySelectorAll(".nav-item");
const views = document.querySelectorAll(".view");

var activateView = function (viewName) {
  navItems.forEach((item) =>
    item.classList.toggle("is-active", item.dataset.view === viewName)
  );
  views.forEach((view) => {
    const isActive = view.id === `view-${viewName}`;
    view.classList.toggle("is-active", isActive);
    view.hidden = !isActive;
  });
};

navItems.forEach((item) =>
  item.addEventListener("click", () => {
    activateView(item.dataset.view);
    if (item.dataset.view === "conversations") loadConversations();
    if (item.dataset.view === "tasks") loadTasks();
    if (item.dataset.view === "agent-activity") loadActivityFromServer();
  })
);

// ---------- Backend health indicator ----------

const statusDot = document.getElementById("api-status-dot");
const statusText = document.getElementById("api-status-text");
const settingApiUrl = document.getElementById("setting-api-url");
const settingApiHealth = document.getElementById("setting-api-health");

function setStatus(kind, text) {
  statusDot.className = `status-dot status-dot--${kind}`;
  statusText.textContent = text;
  settingApiHealth.textContent = text;
}

async function checkHealth() {
  settingApiUrl.textContent = API_BASE_URL;
  try {
    const health = await fetchHealth();
    setStatus("ok", `Online · ${health.status}`);
  } catch (err) {
    setStatus("down", "Offline");
  }
}

checkHealth();
setInterval(checkHealth, 30000);

// ---------- Agent activity trace ----------

const activityRoot = document.getElementById("agent-activity");
const activityEmpty = document.getElementById("activity-empty");

// Renders the safe execution events returned by the backend.
// Only metadata is exposed here — never prompts or raw payloads.
function renderTrace(trace) {
  if (!activityRoot) return;
  if (!trace || trace.length === 0) return;
  activityEmpty && (activityEmpty.hidden = true);

  const table = document.createElement("table");
  table.className = "trace-table";
  table.innerHTML = `
    <thead><tr>
      <th>Time</th><th>Agent</th><th>Action</th><th>Tool</th><th>Status</th><th>Duration</th><th>Error</th>
    </tr></thead>`;
  const tbody = document.createElement("tbody");

  trace.forEach((event) => {
    const row = document.createElement("tr");
    const time = new Date(event.timestamp).toLocaleTimeString();
    const isError = event.status === "error";
    row.innerHTML = `
      <td>${time}</td>
      <td>${event.agent}</td>
      <td>${event.action}</td>
      <td>${event.tool || "—"}</td>
      <td><span class="trace-badge trace-badge--${isError ? "error" : "ok"}">${event.status}</span></td>
      <td>${Math.round(event.duration_ms)} ms</td>
      <td>${isError ? `<span class="trace-error-text">${event.error || ""}</span>` : ""}</td>`;
    tbody.appendChild(row);
  });

  table.appendChild(tbody);
  activityRoot.prepend(table);
}

// ---------- Chat ----------

// The API key lives ONLY on the backend; the frontend just calls our API.
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const chatSend = document.getElementById("chat-send");
const chatMessages = document.getElementById("chat-messages");
const chatStatus = document.getElementById("chat-status");

// Conversation continuity across page reloads (id only — no secrets).
let sessionId = localStorage.getItem("voiceops_session_id") || null;

function addMessage(role, text) {
  const wrap = document.createElement("div");
  wrap.className = `chat-message chat-message--${role}`;
  const bubble = document.createElement("div");
  bubble.className = "chat-bubble";
  bubble.textContent = text; // textContent — never innerHTML, avoids XSS
  wrap.appendChild(bubble);
  chatMessages.appendChild(wrap);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function setChatBusy(busy) {
  chatSend.disabled = busy;
  chatInput.disabled = busy;
}

async function submitChat(message) {
  addMessage("user", message);
  chatStatus.textContent = "Thinking…";
  chatStatus.classList.remove("chat-status--error");
  setChatBusy(true);

  try {
    const body = { message };
    if (sessionId) body.session_id = sessionId;

    const reply = await apiRequest("/api/v1/chat", {
      method: "POST",
      body: JSON.stringify(body),
    });

    sessionId = reply.session_id;
    localStorage.setItem("voiceops_session_id", sessionId);
    addMessage("agent", reply.message);
    renderTrace(reply.trace || []);
    chatStatus.textContent = "";
    return reply;
  } catch (err) {
    chatStatus.textContent = err.message || "Something went wrong.";
    chatStatus.classList.add("chat-status--error");
    return null;
  } finally {
    setChatBusy(false);
    chatInput.focus();
  }
}

chatForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = chatInput.value.trim();
  if (!message || chatSend.disabled) return;
  chatInput.value = "";
  submitChat(message);
});

// ---------- Conversations panel (persisted in Postgres) ----------

const conversationListEl = document.getElementById("conversation-list");
const conversationDetailEl = document.getElementById("conversation-detail");

async function loadConversations() {
  if (!conversationListEl) return;
  conversationListEl.textContent = "Loading…";
  try {
    const conversations = await fetchConversations();
    conversationListEl.textContent = "";
    if (conversations.length === 0) {
      conversationListEl.textContent = "No conversations yet.";
      return;
    }
    conversations.forEach((conversation) => {
      const btn = document.createElement("button");
      btn.className = "conv-item";
      btn.textContent = conversation.title || "Untitled";
      btn.title = conversation.title;
      btn.addEventListener("click", () => openConversation(conversation.id, btn));
      conversationListEl.appendChild(btn);
    });
  } catch (err) {
    conversationListEl.textContent = `Failed to load: ${err.message}`;
  }
}

async function openConversation(id, activeBtn) {
  conversationListEl.querySelectorAll(".conv-item").forEach((el) => el.classList.remove("is-active"));
  activeBtn && activeBtn.classList.add("is-active");
  conversationDetailEl.textContent = "Loading…";
  try {
    const detail = await fetchConversationDetail(id);
    conversationDetailEl.textContent = "";
    detail.messages.forEach((m) => {
      const item = document.createElement("div");
      item.className = `conv-message conv-message--${m.role === "model" ? "model" : "user"}`;
      const role = document.createElement("div");
      role.className = "role";
      role.textContent = m.role;
      const text = document.createElement("div");
      text.textContent = m.content; // textContent — XSS-safe
      item.appendChild(role);
      item.appendChild(text);
      conversationDetailEl.appendChild(item);
    });
  } catch (err) {
    conversationDetailEl.textContent = `Failed to load: ${err.message}`;
  }
}

// ---------- Tasks panel (persisted in Postgres) ----------

const taskListEl = document.getElementById("task-list");
const taskForm = document.getElementById("task-create-form");
const taskTitleInput = document.getElementById("task-title-input");

async function loadTasks() {
  if (!taskListEl) return;
  taskListEl.textContent = "Loading…";
  try {
    const tasks = await fetchTasks();
    taskListEl.textContent = "";
    if (tasks.length === 0) {
      taskListEl.textContent = "No tasks yet — ask the agent or add one above.";
      return;
    }
    tasks.forEach((task) => taskListEl.appendChild(renderTask(task)));
  } catch (err) {
    taskListEl.textContent = `Failed to load: ${err.message}`;
  }
}

function renderTask(task) {
  const row = document.createElement("div");
  row.className = `task-item ${task.status === "completed" ? "task-item--completed" : ""}`;

  const title = document.createElement("span");
  title.className = "task-title";
  title.textContent = task.title;

  const badge = document.createElement("span");
  badge.className = "task-badge";
  badge.textContent = task.status;

  row.appendChild(title);
  row.appendChild(badge);

  if (task.status === "open") {
    const completeBtn = document.createElement("button");
    completeBtn.className = "btn";
    completeBtn.textContent = "✓";
    completeBtn.title = "Mark completed";
    completeBtn.addEventListener("click", async () => {
      completeBtn.disabled = true;
      try {
        await completeTask(task.id);
        row.replaceWith(renderTask({ ...task, status: "completed" }));
      } catch (err) {
        completeBtn.disabled = false;
        alert(`Failed to complete task: ${err.message}`);
      }
    });
    row.appendChild(completeBtn);
  }

  return row;
}

taskForm && taskForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const title = taskTitleInput.value.trim();
  if (!title) return;
  try {
    await createTask(title);
    taskTitleInput.value = "";
    loadTasks();
  } catch (err) {
    alert(`Failed to create task: ${err.message}`);
  }
});

// ---------- Agent activity from the server (agent_runs + tool_calls) ----------

const activityRefreshBtn = document.getElementById("activity-refresh");
const activitySourceEl = document.getElementById("activity-source");

function renderServerActivity(runs) {
  if (!activityRoot) return;
  activityEmpty && (activityEmpty.hidden = true);
  const table = document.createElement("table");
  table.className = "trace-table";
  table.innerHTML = `
    <thead><tr>
      <th>Started</th><th>Agent</th><th>Tool</th><th>Status</th><th>Error/Info</th>
    </tr></thead>`;
  const tbody = document.createElement("tbody");

  runs.forEach((run) => {
    if (run.tool_calls.length === 0) {
      const row = document.createElement("tr");
      row.innerHTML = `
        <td>${run.started_at ? new Date(run.started_at).toLocaleString() : "—"}</td>
        <td>${run.agent_name}</td><td>—</td>
        <td><span class="trace-badge trace-badge--ok">${run.status}</span></td><td></td>`;
      tbody.appendChild(row);
      return;
    }
    run.tool_calls.forEach((call) => {
      const row = document.createElement("tr");
      const isError = call.status === "error";
      row.innerHTML = `
        <td>${call.created_at ? new Date(call.created_at).toLocaleString() : "—"}</td>
        <td>${run.agent_name}</td><td>${call.tool_name}</td>
        <td><span class="trace-badge trace-badge--${isError ? "error" : "ok"}">${call.status}</span></td>
        <td>${isError || (call.output_data && call.output_data.error) ? `<span class="trace-error-text">${(call.output_data && call.output_data.error) || ""}</span>` : (call.output_data && call.output_data.duration_ms != null ? `${Math.round(call.output_data.duration_ms)} ms` : "")}</td>`;
      tbody.appendChild(row);
    });
  });

  table.appendChild(tbody);
  activityRoot.prepend(table);
}

async function loadActivityFromServer() {
  if (!activityRoot) return;
  try {
    const runs = await fetchActivity();
    activitySourceEl.textContent = `Loaded ${runs.length} agent runs from the database`;
    if (runs.length > 0) renderServerActivity(runs);
  } catch (err) {
    activitySourceEl.textContent = `Failed to load history: ${err.message}`;
  }
}

activityRefreshBtn && activityRefreshBtn.addEventListener("click", loadActivityFromServer);
