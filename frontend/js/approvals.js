/**
 * Approvals panel + Google connection status (Part 6).
 *
 * Human-in-the-loop: pending approvals show action, tool, payload details,
 * and risk. High-risk actions (e.g. send_email, delete_event) require an
 * explicit typed-confirmation step before Approve is even enabled.
 */

const approvalsList = document.getElementById("approvals-list");
const approvalsEmpty = document.getElementById("approvals-empty");
const approvalsRefresh = document.getElementById("approvals-refresh");
const auditList = document.getElementById("audit-list");
const googleStatusEl = document.getElementById("google-status");
const googleConnectLink = document.getElementById("google-connect-link");
const googleDisconnectBtn = document.getElementById("google-disconnect");

// ---------- Approvals ----------

async function loadApprovals() {
  if (!approvalsList) return;
  approvalsList.textContent = "Loading…";
  try {
    const approvals = await fetchApprovals();
    approvalsList.textContent = "";
    approvalsEmpty.hidden = approvals.length > 0;
    approvals.forEach((approval) => approvalsList.appendChild(renderApproval(approval)));
  } catch (err) {
    approvalsList.textContent = `Failed to load approvals: ${err.message}`;
  }
}

function renderApproval(approval) {
  const card = document.createElement("div");
  card.className = `approval-card approval-card--${approval.risk_level}`;

  const head = document.createElement("div");
  head.className = "approval-head";
  const tool = document.createElement("span");
  tool.className = "approval-tool";
  tool.textContent = approval.tool_name;
  const risk = document.createElement("span");
  risk.className = `risk-badge risk-badge--${approval.risk_level}`;
  risk.textContent = approval.risk_level;
  const status = document.createElement("span");
  status.className = "status-chip";
  status.textContent = approval.status;
  head.appendChild(tool);
  head.appendChild(risk);
  head.appendChild(status);
  card.appendChild(head);

  const summary = document.createElement("div");
  summary.textContent = approval.summary || approval.action;
  card.appendChild(summary);

  const details = document.createElement("pre");
  details.className = "approval-details";
  details.textContent = JSON.stringify(approval.payload, null, 2);
  card.appendChild(details);

  const isPending = approval.status === "pending";

  // Edit mode: modify the payload before approving.
  let editBox = null;
  function enterEditMode() {
    if (editBox) return;
    editBox = document.createElement("div");
    editBox.className = "approval-edit";
    const textarea = document.createElement("textarea");
    textarea.value = JSON.stringify(approval.payload, null, 2);
    const apply = document.createElement("button");
    apply.className = "btn btn--primary";
    apply.textContent = "Save & approve edited";
    apply.addEventListener("click", () => {
      let edited;
      try {
        edited = JSON.parse(textarea.value);
      } catch (err) {
        alert("Payload is not valid JSON: " + err.message);
        return;
      }
      confirmAndResolve("approved", edited);
    });
    const cancel = document.createElement("button");
    cancel.className = "btn";
    cancel.textContent = "Cancel edit";
    cancel.addEventListener("click", () => { editBox.remove(); editBox = null; });
    editBox.appendChild(textarea);
    editBox.appendChild(apply);
    editBox.appendChild(cancel);
    card.appendChild(editBox);
  }

  function confirmAndResolve(decision, editedPayload) {
    if (approval.risk_level === "high" && decision === "approved") {
      const typed = prompt(
        `HIGH-RISK ACTION: ${approval.summary}\n\nType APPROVE (in capitals) to confirm:`
      );
      if (typed !== "APPROVE") return;
    }
    resolveApproval(approval.id, decision, approval.risk_level === "high", editedPayload)
      .then((result) => {
        if (result.executed) {
          alert("Action executed: " + JSON.stringify(result.result));
        } else if (result.status === "rejected") {
          // silent; list refresh shows it
        } else if (result.error) {
          alert("Execution failed: " + result.error);
        }
        loadApprovals();
        loadAudit();
      })
      .catch((err) => alert(err.message));
  }

  if (isPending) {
    const actions = document.createElement("div");
    actions.className = "approval-actions";

    const approveBtn = document.createElement("button");
    approveBtn.className = "btn btn--primary";
    approveBtn.textContent = "Approve";
    approveBtn.addEventListener("click", () => confirmAndResolve("approved"));

    const rejectBtn = document.createElement("button");
    rejectBtn.className = "btn";
    rejectBtn.textContent = "Reject";
    rejectBtn.addEventListener("click", () => confirmAndResolve("rejected"));

    const editBtn = document.createElement("button");
    editBtn.className = "btn";
    editBtn.textContent = "Edit";
    editBtn.addEventListener("click", enterEditMode);

    actions.appendChild(approveBtn);
    actions.appendChild(rejectBtn);
    actions.appendChild(editBtn);
    card.appendChild(actions);

    if (approval.risk_level === "high") {
      const warn = document.createElement("div");
      warn.className = "confirm-box";
      warn.textContent = "⚠ High-risk action: approving requires typed confirmation (APPROVE).";
      card.appendChild(warn);
    }
  }

  return card;
}

// ---------- Audit trail ----------

async function loadAudit() {
  if (!auditList) return;
  try {
    const rows = await fetchAudit(50);
    auditList.textContent = "";
    if (rows.length === 0) {
      const p = document.createElement("p");
      p.className = "placeholder-text";
      p.textContent = "No audit events yet.";
      auditList.appendChild(p);
      return;
    }
    const table = document.createElement("table");
    table.className = "trace-table";
    table.innerHTML = "<thead><tr><th>Time</th><th>Event</th><th>Tool</th><th>Detail</th></tr></thead>";
    const tbody = document.createElement("tbody");
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      const time = row.created_at ? new Date(row.created_at).toLocaleString() : "—";
      tr.innerHTML = `<td>${time}</td><td>${row.event}</td><td>${row.tool_name || "—"}</td><td>${JSON.stringify(row.detail)}</td>`;
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    auditList.appendChild(table);
  } catch (err) {
    auditList.textContent = `Failed to load audit: ${err.message}`;
  }
}

// ---------- Google connection status ----------

async function refreshGoogleStatus() {
  if (!googleStatusEl) return;
  try {
    const status = await fetchGoogleStatus();
    if (!status.configured) {
      googleStatusEl.textContent = "not configured (set GOOGLE_CLIENT_ID/SECRET)";
      googleConnectLink.style.display = "none";
      googleDisconnectBtn.style.display = "none";
      return;
    }
    googleStatusEl.textContent = status.connected ? "connected ✓" : "not connected";
    googleConnectLink.style.display = status.connected ? "none" : "inline-block";
    googleDisconnectBtn.style.display = status.connected ? "inline-block" : "none";
    googleConnectLink.href = `${API_BASE_URL}/api/v1/auth/google/login`;
  } catch (err) {
    googleStatusEl.textContent = `error: ${err.message}`;
  }
}

googleDisconnectBtn && googleDisconnectBtn.addEventListener("click", async () => {
  try {
    await disconnectGoogle();
    refreshGoogleStatus();
  } catch (err) {
    alert(err.message);
  }
});

approvalsRefresh && approvalsRefresh.addEventListener("click", () => { loadApprovals(); loadAudit(); });

// Refresh approvals when navigating to the tab; reflect OAuth result hash.
const originalActivateView = activateView;
activateView = function (viewName) {
  originalActivateView(viewName);
  if (viewName === "approvals") { loadApprovals(); loadAudit(); }
  if (viewName === "settings") { refreshGoogleStatus(); }
  if (viewName === "documents") { loadDocuments(); }
};

if (window.location.hash.startsWith("#google_auth_")) {
  const result = window.location.hash.slice(1);
  googleStatusEl && (googleStatusEl.textContent =
    result === "google_auth_success" ? "connected ✓" : `auth failed (${result})`);
}
refreshGoogleStatus();
