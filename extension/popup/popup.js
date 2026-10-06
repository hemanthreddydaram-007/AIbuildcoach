/**
 * Popup UI Controller for AI Build Coach Browser Extension.
 *
 * Enforces strictly user-controlled capture and preview-before-transfer invariant:
 * 1. Checks provider compatibility purely via activeTab.url (NO script injection on popup open).
 * 2. Injects capture script ONLY after user clicks "Capture Conversation" via chrome.scripting.executeScript.
 * 3. Previews in-memory; discards payload if popup closes or user cancels.
 * 4. Transfers ONLY after explicit user confirmation ("Send to Build Coach").
 */

import { defaultAdapterFactory } from "../src/adapters/factory.js";
import { MessageType, createMessage } from "../src/messages.js";
import { generatePreview, prepareTransferPayload } from "../src/bridge.js";
import {
  hasLocalPermission,
  requestLocalPermission,
  checkConnection,
  sendCapture,
  listProjects,
  bindConversation,
  getBindingStatus,
  getSession,
  refreshSession,
  getUnderstanding,
} from "../src/bridge/local_bridge.js";

// DOM Elements
const providerBadge = document.getElementById("provider-badge");
const pageTitleEl = document.getElementById("page-title");
const btnCapture = document.getElementById("btn-capture");
const btnSend = document.getElementById("btn-send");
const btnCopyJson = document.getElementById("btn-copy-json");
const btnCancel = document.getElementById("btn-cancel");
const btnDone = document.getElementById("btn-done");
const selectProject = document.getElementById("select-project");
const selectBindProject = document.getElementById("select-bind-project");
const btnBindProject = document.getElementById("btn-bind-project");
const btnSkipBind = document.getElementById("btn-skip-bind");
const bindProjectSection = document.getElementById("bind-project-section");
const bindingStatusText = document.getElementById("binding-status-text");

// States
const stateUnsupported = document.getElementById("state-unsupported");
const stateReady = document.getElementById("state-ready");
const stateCapturing = document.getElementById("state-capturing");
const statePreview = document.getElementById("state-preview");
const stateSuccess = document.getElementById("state-success");
const errorBanner = document.getElementById("error-banner");
const errorText = document.getElementById("error-text");

// Preview Elements
const previewProvider = document.getElementById("preview-provider");
const previewSecrets = document.getElementById("preview-secrets");
const previewTitle = document.getElementById("preview-title");
const previewMsgCount = document.getElementById("preview-msg-count");
const roleFirst = document.getElementById("role-first");
const contentFirst = document.getElementById("content-first");
const roleLast = document.getElementById("role-last");
const contentLast = document.getElementById("content-last");

// Ephemeral in-memory state (NEVER persisted or transferred without confirmation)
let activeTab = null;
let currentPayload = null;

function showState(targetState) {
  [stateUnsupported, stateReady, stateCapturing, statePreview, stateSuccess].forEach((s) => {
    s.classList.add("hidden");
  });
  targetState.classList.remove("hidden");
  errorBanner.classList.add("hidden");
}

function showError(msg) {
  errorText.textContent = msg;
  errorBanner.classList.remove("hidden");
}

function setProviderBadge(provider) {
  providerBadge.className = "badge";
  if (provider === "CHATGPT") {
    providerBadge.classList.add("badge-chatgpt");
    providerBadge.textContent = "ChatGPT";
  } else if (provider === "CLAUDE") {
    providerBadge.classList.add("badge-claude");
    providerBadge.textContent = "Claude";
  } else if (provider === "GEMINI") {
    providerBadge.classList.add("badge-gemini");
    providerBadge.textContent = "Gemini";
  } else {
    providerBadge.classList.add("badge-unsupported");
    providerBadge.textContent = "Unsupported";
  }
}

// 1. Initial status check on popup open (Zero script execution, purely URL based)
document.addEventListener("DOMContentLoaded", async () => {
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tabs || tabs.length === 0) {
      showState(stateUnsupported);
      return;
    }

    activeTab = tabs[0];
    const url = activeTab.url || "";
    const adapter = defaultAdapterFactory.getAdapter(url);

    if (!adapter) {
      setProviderBadge(null);
      showState(stateUnsupported);
      return;
    }

    const provider = adapter.getProvider();
    setProviderBadge(provider);
    pageTitleEl.textContent = activeTab.title || `${provider} Conversation`;
    showState(stateReady);
  } catch (err) {
    showError("Could not inspect active tab: " + err.message);
    showState(stateUnsupported);
  }
});

// 2. Explicit User Action: Capture Conversation via chrome.scripting.executeScript
btnCapture.addEventListener("click", async () => {
  if (!activeTab || !activeTab.id) return;

  showState(stateCapturing);

  try {
    const [injectionResult] = await chrome.scripting.executeScript({
      target: { tabId: activeTab.id },
      files: ["content_script.js"],
    });

    const response = injectionResult?.result;
    if (!response || !response.success || response.status === "CAPTURE_UNAVAILABLE") {
      const errMsg =
        response?.error?.message ||
        "Conversation capture unavailable: Could not locate active conversation messages on this page.";
      showError(errMsg);
      showState(stateReady);
      return;
    }

    currentPayload = response.payload.conversation;
    renderPreview(currentPayload);
    showState(statePreview);
  } catch (err) {
    showError("Failed to execute in-page capture: " + err.message);
    showState(stateReady);
  }
});

// 3. Render Preview In Memory
function renderPreview(payload) {
  const projectId = inputProjectId.value.trim() || null;
  const preview = generatePreview(payload, projectId);

  previewProvider.textContent = preview.provider;
  previewTitle.textContent = preview.title;
  previewMsgCount.textContent = preview.messageCount;

  if (preview.detectedSecretCount > 0) {
    previewSecrets.textContent = `${preview.detectedSecretCount} Secret(s) Redacted`;
    previewSecrets.classList.remove("hidden");
  } else {
    previewSecrets.textContent = "0 Secrets Detected";
    previewSecrets.classList.add("hidden");
  }

  if (preview.firstMessagePreview) {
    roleFirst.textContent = preview.firstMessagePreview.role;
    contentFirst.textContent = preview.firstMessagePreview.snippet;
  }

  if (preview.lastMessagePreview) {
    roleLast.textContent = preview.lastMessagePreview.role;
    contentLast.textContent = preview.lastMessagePreview.snippet;
  }

  btnSend.disabled = !preview.canTransfer;

  // Asynchronously populate project dropdown if permission is already granted or bridge is accessible
  loadProjectDropdowns();
}

let cachedProjects = [];
let lastStoredConversationId = null;

async function loadProjectDropdowns() {
  try {
    const hasPerm = await hasLocalPermission();
    if (!hasPerm) return;
    const projRes = await listProjects();
    if (projRes.ok && Array.isArray(projRes.projects)) {
      cachedProjects = projRes.projects;
      populateSelectOptions(selectProject, cachedProjects, true);
      populateSelectOptions(selectBindProject, cachedProjects, false);
    }
  } catch (err) {
    // Non-fatal, user can still proceed or bind later
  }
}

function populateSelectOptions(selectEl, projects, includeUnboundOption) {
  if (!selectEl) return;
  const currentVal = selectEl.value;
  selectEl.innerHTML = "";
  if (includeUnboundOption) {
    const optNone = document.createElement("option");
    optNone.value = "";
    optNone.textContent = "[ None / Unbound ]";
    selectEl.appendChild(optNone);
  } else {
    const optPrompt = document.createElement("option");
    optPrompt.value = "";
    optPrompt.textContent = "[ Select a local project ▼ ]";
    selectEl.appendChild(optPrompt);
  }

  projects.forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p.project_id;
    opt.textContent = p.display_name;
    selectEl.appendChild(opt);
  });

  if (currentVal) {
    selectEl.value = currentVal;
  }
}

// 4. Explicit User Confirmation: Send to Build Coach
btnSend.addEventListener("click", async () => {
  if (!currentPayload) return;

  const projectId = selectProject?.value || null;
  const finalPayload = prepareTransferPayload(currentPayload, projectId);

  // 1. Check or request optional permission for http://127.0.0.1/*
  const hasPerm = await hasLocalPermission();
  if (!hasPerm) {
    const granted = await requestLocalPermission();
    if (!granted) {
      showError("Connection permission to local Build Coach (127.0.0.1) was not granted. Use 'Copy JSON' to transfer manually.");
      return;
    }
  }

  // 2. Health check
  const health = await checkConnection();
  if (!health.connected) {
    showError("Build Coach is not running.\nStart it locally with:\npython -m backend.cli bridge start");
    return;
  }

  // 3. Send capture payload to local bridge
  const transferResult = await sendCapture(finalPayload);
  if (!transferResult.ok) {
    showError(transferResult.error?.message || "Transfer to local bridge failed.");
    return;
  }

  lastStoredConversationId = transferResult.result?.conversation_id || null;

  // 4. Update success message and view
  const successDesc = document.getElementById("success-desc");
  if (successDesc && transferResult.result) {
    successDesc.textContent = `Conversation '${transferResult.result.conversation_id}' (${transferResult.result.message_count} messages) securely stored in Build Coach.`;
  }

  // 5. Check and display binding status
  if (lastStoredConversationId) {
    const bindStatusRes = await getBindingStatus(lastStoredConversationId);
    if (bindStatusRes.ok && bindStatusRes.result?.bound && bindStatusRes.result?.project) {
      bindingStatusText.textContent = `Bound to '${bindStatusRes.result.project.display_name}'`;
      bindProjectSection.classList.add("hidden");
    } else {
      bindingStatusText.textContent = "Not bound";
      // Ensure projects are loaded in selectBindProject
      await loadProjectDropdowns();
      if (cachedProjects.length > 0) {
        bindProjectSection.classList.remove("hidden");
      } else {
        bindProjectSection.classList.add("hidden");
      }
    }
  }

  showState(stateSuccess);
});

// 5. Bind Project Action on Success Screen
btnBindProject.addEventListener("click", async () => {
  const chosenProjectId = selectBindProject?.value;
  if (!chosenProjectId) {
    showError("Please select a local project to bind.");
    return;
  }
  if (!lastStoredConversationId) {
    showError("No stored conversation to bind.");
    return;
  }

  const res = await bindConversation(lastStoredConversationId, chosenProjectId);
  if (!res.ok) {
    showError(res.error?.message || "Binding conversation to project failed.");
    return;
  }

  const chosenProjObj = cachedProjects.find((p) => p.project_id === chosenProjectId);
  const displayName = chosenProjObj ? chosenProjObj.display_name : chosenProjectId;
  bindingStatusText.textContent = `Bound to '${displayName}'`;
  bindProjectSection.classList.add("hidden");
  errorBanner.classList.add("hidden");
});

// 6. Skip Binding Action
btnSkipBind.addEventListener("click", () => {
  bindProjectSection.classList.add("hidden");
});

// 7. Copy JSON Action
btnCopyJson.addEventListener("click", async () => {
  if (!currentPayload) return;

  const projectId = selectProject?.value || null;
  const finalPayload = prepareTransferPayload(currentPayload, projectId);
  const jsonStr = JSON.stringify(finalPayload, null, 2);

  try {
    await navigator.clipboard.writeText(jsonStr);
    const origText = btnCopyJson.textContent;
    btnCopyJson.textContent = "✅ Copied!";
    setTimeout(() => {
      btnCopyJson.textContent = origText;
    }, 2000);
  } catch (err) {
    showError("Clipboard copy failed: " + err.message);
  }
});

// 8. Cancel Action
btnCancel.addEventListener("click", () => {
  currentPayload = null;
  lastStoredConversationId = null;
  showState(stateReady);
});

// 9. Done Action
btnDone.addEventListener("click", () => {
  window.close();
});

// ============================================================================
// M12.11 Build Coach Session View Controller
// ============================================================================
const tabCapture = document.getElementById("tab-capture");
const tabSession = document.getElementById("tab-session");
const containerCapture = document.getElementById("container-capture");
const viewSession = document.getElementById("view-session");
const selectSessionProject = document.getElementById("select-session-project");
const btnRefreshSession = document.getElementById("btn-refresh-session");
const sessionLoading = document.getElementById("session-loading");
const sessionEmpty = document.getElementById("session-empty");
const sessionDetails = document.getElementById("session-details");

const sessStateBadge = document.getElementById("sess-state-badge");
const sessLatestIncident = document.getElementById("sess-latest-incident");
const sessFixStatus = document.getElementById("sess-fix-status");
const sessWhatHappened = document.getElementById("sess-what-happened");
const btnToggleExplain = document.getElementById("btn-toggle-explain");
const sessExplainExpanded = document.getElementById("sess-explain-expanded");
const sessEvidenceList = document.getElementById("sess-evidence-list");
const sessUnknownsList = document.getElementById("sess-unknowns-list");
const sessActionPriority = document.getElementById("sess-action-priority");
const sessActionTitle = document.getElementById("sess-action-title");
const sessActionWhy = document.getElementById("sess-action-why");
const sessUndBadge = document.getElementById("sess-und-badge");
const sessUndDesc = document.getElementById("sess-und-desc");
const btnStartUnderstand = document.getElementById("btn-start-understand");
const sessActivityList = document.getElementById("sess-activity-list");

let currentSession = null;

function renderSession(sessionData) {
  if (!sessionData) return;
  currentSession = sessionData;

  // State badge
  if (sessStateBadge) {
    sessStateBadge.textContent = sessionData.state || "UNKNOWN";
    sessStateBadge.className = "badge";
    const st = sessionData.state;
    if (st === "STABLE") sessStateBadge.style.backgroundColor = "rgba(16, 185, 129, 0.2)";
    else if (st === "INVESTIGATING") sessStateBadge.style.backgroundColor = "rgba(239, 68, 68, 0.2)";
    else if (st === "VERIFYING") sessStateBadge.style.backgroundColor = "rgba(245, 158, 11, 0.2)";
    else if (st === "LEARNING") sessStateBadge.style.backgroundColor = "rgba(139, 92, 246, 0.2)";
    else sessStateBadge.style.backgroundColor = "rgba(59, 130, 246, 0.2)";
  }

  // Latest incident
  if (sessLatestIncident) {
    const inc = sessionData.active_incident;
    if (inc) {
      sessLatestIncident.textContent = inc.error_type || inc.message || "Active Error";
    } else if (sessionData.verification && sessionData.verification.status !== "UNKNOWN") {
      sessLatestIncident.textContent = sessionData.verification.status;
    } else {
      sessLatestIncident.textContent = "None";
    }
  }

  // Status / FixStatus
  if (sessFixStatus) {
    sessFixStatus.textContent = sessionData.fix_status || "UNKNOWN";
  }

  // What Happened?
  if (sessWhatHappened) {
    sessWhatHappened.textContent = sessionData.what_happened || "No active incidents detected.";
  }

  // Expanded explanation details
  if (sessExplainExpanded) {
    const exp = sessionData.incident_explanation;
    if (exp && (exp.problem || exp.observed_sequence || exp.changes)) {
      let html = "";
      if (exp.problem && exp.problem.length > 0) {
        html += `<div style="margin-bottom: 6px;"><strong>Problem:</strong><ul style="padding-left: 14px;">${exp.problem.map((p) => `<li>${p.statement || p}</li>`).join("")}</ul></div>`;
      }
      if (exp.changes && exp.changes.length > 0) {
        html += `<div style="margin-bottom: 6px;"><strong>Changes:</strong><ul style="padding-left: 14px;">${exp.changes.map((c) => `<li>${c.statement || c}</li>`).join("")}</ul></div>`;
      }
      if (exp.verification && exp.verification.length > 0) {
        html += `<div><strong>Verification:</strong><ul style="padding-left: 14px;">${exp.verification.map((v) => `<li>${v.statement || v}</li>`).join("")}</ul></div>`;
      }
      sessExplainExpanded.innerHTML = html;
    } else {
      sessExplainExpanded.textContent = sessionData.what_happened || "No detailed breakdown available.";
    }
  }

  // How Do We Know? (Evidence)
  if (sessEvidenceList) {
    sessEvidenceList.innerHTML = "";
    const items = sessionData.how_do_we_know || [];
    if (items.length > 0) {
      items.forEach((item) => {
        const li = document.createElement("li");
        li.textContent = item.startsWith("✓") ? item : `✓ ${item}`;
        sessEvidenceList.appendChild(li);
      });
    } else {
      const li = document.createElement("li");
      li.textContent = "✓ No runtime errors observed.";
      sessEvidenceList.appendChild(li);
    }
  }

  // Still Unknown
  if (sessUnknownsList) {
    sessUnknownsList.innerHTML = "";
    const unknowns = sessionData.summary?.unknowns || [];
    if (unknowns.length > 0) {
      unknowns.forEach((u) => {
        const li = document.createElement("li");
        li.textContent = `- ${u}`;
        sessUnknownsList.appendChild(li);
      });
    } else {
      const li = document.createElement("li");
      li.textContent = "- No critical unknowns recorded.";
      sessUnknownsList.appendChild(li);
    }
  }

  // Next Action
  if (sessActionTitle && sessActionWhy) {
    const act = sessionData.next_action;
    if (act) {
      sessActionTitle.textContent = act.title;
      sessActionWhy.textContent = act.description || "";
      if (sessActionPriority) {
        sessActionPriority.textContent = act.priority || "NORMAL";
      }
    } else {
      sessActionTitle.textContent = "No immediate action required.";
      sessActionWhy.textContent = "";
      if (sessActionPriority) sessActionPriority.textContent = "NONE";
    }
  }

  // Do I Understand?
  if (sessUndBadge && sessUndDesc) {
    const und = sessionData.understanding;
    if (und) {
      if (und.latest_state === "UNDERSTOOD") {
        sessUndBadge.textContent = "UNDERSTOOD";
        sessUndBadge.style.backgroundColor = "rgba(16, 185, 129, 0.2)";
        sessUndDesc.textContent = "Strong comprehension verified.";
      } else if (und.required) {
        sessUndBadge.textContent = "REVIEW NEEDED";
        sessUndBadge.style.backgroundColor = "rgba(245, 158, 11, 0.2)";
        sessUndDesc.textContent = "Recent changes require conceptual verification.";
      } else {
        sessUndBadge.textContent = "PENDING";
        sessUndBadge.style.backgroundColor = "rgba(148, 163, 184, 0.2)";
        sessUndDesc.textContent = "Comprehension check available.";
      }
    }
  }

  // Recent Activity
  if (sessActivityList) {
    sessActivityList.innerHTML = "";
    const activities = sessionData.recent_activity || [];
    if (activities.length > 0) {
      activities.forEach((act) => {
        const li = document.createElement("li");
        const t = act.time_short || (act.timestamp ? act.timestamp.slice(11, 16) : "");
        li.innerHTML = `<span>${act.summary || act.event_type}</span><span style="color: #64748b; font-size: 10px;">${t}</span>`;
        sessActivityList.appendChild(li);
      });
    } else {
      const li = document.createElement("li");
      li.textContent = "No recent activity recorded.";
      sessActivityList.appendChild(li);
    }
  }
}

async function loadSessionForProject(projectId) {
  if (!projectId) {
    sessionDetails.classList.add("hidden");
    sessionEmpty.classList.remove("hidden");
    return;
  }
  sessionLoading.classList.remove("hidden");
  sessionEmpty.classList.add("hidden");
  sessionDetails.classList.add("hidden");

  const res = await getSession(projectId);
  sessionLoading.classList.add("hidden");
  if (!res.ok) {
    showError(res.error?.message || "Could not retrieve Build Coach session.");
    sessionEmpty.classList.remove("hidden");
    return;
  }

  renderSession(res.result);
  sessionDetails.classList.remove("hidden");
}

async function initSessionTab() {
  const perm = await hasLocalPermission();
  if (!perm) {
    const granted = await requestLocalPermission();
    if (!granted) {
      showError("Local bridge permission required to connect to Build Coach.");
      return;
    }
  }

  const health = await checkConnection();
  if (!health.connected) {
    showError("Build Coach is not running.\nStart it locally with: python -m backend.cli bridge start");
    sessionEmpty.classList.remove("hidden");
    return;
  }

  await loadProjectDropdowns();
  if (selectSessionProject) {
    selectSessionProject.innerHTML = '<option value="">[ Select Project ▼ ]</option>';
    cachedProjects.forEach((p) => {
      const opt = document.createElement("option");
      opt.value = p.project_id;
      opt.textContent = `${p.display_name} (${p.project_id})`;
      selectSessionProject.appendChild(opt);
    });

    if (cachedProjects.length > 0) {
      selectSessionProject.value = cachedProjects[0].project_id;
      await loadSessionForProject(cachedProjects[0].project_id);
    } else {
      sessionEmpty.classList.remove("hidden");
    }
  }
}

// Tab click listeners
if (tabCapture && tabSession) {
  tabCapture.addEventListener("click", () => {
    tabCapture.classList.add("active");
    tabSession.classList.remove("active");
    containerCapture?.classList.remove("hidden");
    viewSession?.classList.add("hidden");
  });

  tabSession.addEventListener("click", async () => {
    tabSession.classList.add("active");
    tabCapture.classList.remove("active");
    containerCapture?.classList.add("hidden");
    viewSession?.classList.remove("hidden");
    await initSessionTab();
  });
}

if (selectSessionProject) {
  selectSessionProject.addEventListener("change", async () => {
    await loadSessionForProject(selectSessionProject.value);
  });
}

if (btnRefreshSession) {
  btnRefreshSession.addEventListener("click", async () => {
    const projId = selectSessionProject?.value;
    if (!projId) return;
    sessionLoading.classList.remove("hidden");
    const res = await refreshSession(projId);
    sessionLoading.classList.add("hidden");
    if (res.ok) {
      renderSession(res.result);
    } else {
      showError(res.error?.message || "Failed to refresh session.");
    }
  });
}

if (btnToggleExplain) {
  btnToggleExplain.addEventListener("click", () => {
    if (sessExplainExpanded.classList.contains("hidden")) {
      sessExplainExpanded.classList.remove("hidden");
      btnToggleExplain.textContent = "Hide ▲";
    } else {
      sessExplainExpanded.classList.add("hidden");
      btnToggleExplain.textContent = "Details ▼";
    }
  });
}

if (btnStartUnderstand) {
  btnStartUnderstand.addEventListener("click", async () => {
    const projId = selectSessionProject?.value;
    if (!projId) return;
    const res = await getUnderstanding(projId);
    if (res.ok && res.result) {
      if (sessUndDesc) {
        sessUndDesc.textContent = res.result.overall_state === "UNDERSTOOD"
          ? "Strong comprehension verified."
          : `State: ${res.result.overall_state || "Pending"}. Requires explanation.`;
      }
    }
  });
}


