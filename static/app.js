const state = {
  me: null,
  csrf: "",
  view: "monitor",
  overview: null,
  banner: "",
  bannerOk: false,
};

let renderedView = null;
let pollTimer = null;
let liveSignature = "";

const STATUS_CLASS = {
  successful: "ok",
  ready: "ok",
  available: "ok",
  succeeded: "ok",
  cooldown: "warn",
  rate_limited: "warn",
  authenticating: "warn",
  checking: "info",
  attempting: "info",
  unavailable: "",
  failed: "bad",
  auth_error: "bad",
  authentication_error: "bad",
  blocked: "bad",
  unknown_error: "bad",
  needs_login: "warn",
};

const STATUS_LABELS = {
  unavailable: "Unavailable",
  checking: "Checking",
  attempting: "Attempting claim",
  successful: "Successful",
  failed: "Failed",
  authentication_error: "Authentication error",
  auth_error: "Authentication error",
  rate_limited: "Rate limited",
  unknown_error: "Unknown error",
  ready: "Ready",
  cooldown: "Cooldown",
  needs_login: "Needs login",
  authenticating: "Authenticating",
  blocked: "Blocked",
  disabled: "Disabled",
  error: "Error",
};

document.addEventListener("submit", onSubmit);
document.addEventListener("click", onClick);

boot();

async function boot() {
  const response = await fetch("/api/me");
  if (response.ok) {
    state.me = await response.json();
    state.csrf = state.me.csrf_token;
    await refresh();
    ensurePoll();
    return;
  }
  paint();
}

async function refresh() {
  if (!state.me) return;
  const response = await fetch("/api/overview");
  if (response.status === 401) {
    state.me = null;
    renderedView = null;
    paint();
    return;
  }
  if (!response.ok) return;
  state.overview = await response.json();
  paint();
}

function paint() {
  const root = document.getElementById("app");
  if (!state.me) {
    if (renderedView !== "login") {
      root.innerHTML = loginHtml();
      renderedView = "login";
    }
    const banner = document.getElementById("banner");
    if (banner) setBanner(banner);
    return;
  }
  if (renderedView !== state.view) {
    root.innerHTML = shellHtml();
    renderedView = state.view;
    liveSignature = "";
  }
  updateChrome();
  const live = document.getElementById("live");
  if (!live || editingInside(live)) return;
  const signature = liveSignatureFor();
  if (signature === liveSignature) return;
  liveSignature = signature;
  live.innerHTML = liveHtml();
}

function liveSignatureFor() {
  if (!state.overview) return "loading";
  const overview = state.overview;
  return JSON.stringify({
    view: state.view,
    running: overview.sniper_running,
    accounts: overview.accounts,
    targets: overview.targets,
    activity: state.view === "monitor" ? overview.activity.slice(0, 8) : overview.activity,
    jobs: overview.jobs,
    settings: overview.settings,
    milestone: overview.milestone,
    workerFresh: heartbeatFresh(overview.worker && overview.worker.heartbeat),
  });
}

function editingInside(live) {
  const active = document.activeElement;
  if (!active || !live.contains(active)) return false;
  return active.tagName === "INPUT" || active.tagName === "TEXTAREA" || active.tagName === "SELECT";
}

function loginHtml() {
  return `
    <div class="login-wrap">
      <form class="login-card stack" data-action="login">
        <div>
          <div class="eyebrow">Minecraft</div>
          <h1>Username monitor</h1>
          <p class="lede">Sign in with the operator password from the server environment. Minecraft accounts are connected after this, on Microsoft's own site.</p>
        </div>
        <div id="banner" class="banner hidden" role="alert"></div>
        <label>Operator password
          <input name="password" type="password" autocomplete="current-password" required>
        </label>
        <button class="btn" type="submit">Enter</button>
      </form>
    </div>`;
}

function shellHtml() {
  const views = [
    ["monitor", "Monitor"],
    ["accounts", "Accounts"],
    ["targets", "Targets"],
    ["activity", "Activity"],
    ["settings", "Settings"],
  ];
  return `
    <div class="shell">
      <aside class="rail">
        <div class="brand">
          <div class="eyebrow">Field log</div>
          <strong>Username monitor</strong>
        </div>
        <nav>
          ${views.map(([id, label]) => `<button type="button" data-view="${id}" class="${state.view === id ? "active" : ""}">${label}</button>`).join("")}
        </nav>
      </aside>
      <main class="main">
        <header class="topbar">
          <div class="row">
            <span id="run-lamp" class="lamp"></span>
            <div>
              <div class="eyebrow">Sniper</div>
              <strong id="run-label">Loading</strong>
            </div>
          </div>
          <div class="row">
            <button class="btn" type="button" data-action="start">Start</button>
            <button class="btn secondary" type="button" data-action="stop">Stop</button>
            <button class="btn quiet" type="button" data-action="logout">Sign out</button>
          </div>
        </header>
        <div id="banner" class="banner hidden" role="alert"></div>
        ${viewIntro()}
        <div id="live"></div>
      </main>
    </div>`;
}

function viewIntro() {
  if (state.view === "accounts") {
    return `
      <section class="panel">
        <h2>Connect an account</h2>
        <p class="help">You sign in at microsoft.com. This app stores an encrypted refresh token and never asks for the Minecraft password.</p>
        <form class="form-grid" data-action="add-account" style="margin-top:14px">
          <label>Label
            <input name="label" maxlength="80" placeholder="Main, alt 2…" required>
          </label>
          <div></div>
          <div></div>
          <button class="btn" type="submit">Connect Microsoft</button>
        </form>
      </section>`;
  }
  if (state.view === "targets") {
    return `
      <section class="panel">
        <h2>Add a username</h2>
        <p class="help">Names are checked with the official availability endpoint. A release hint only slows checks until the window around that time. It never triggers a claim by itself.</p>
        <form class="form-grid" data-action="add-target" style="margin-top:14px">
          <label>Username
            <input name="username" minlength="3" maxlength="16" placeholder="abc" required>
          </label>
          <label>Priority
            <select name="priority">
              <option value="auto">Auto</option>
              <option value="3c">3-character</option>
              <option value="og">OG</option>
              <option value="normal">Normal</option>
            </select>
          </label>
          <label>Release hint (optional)
            <input name="release_hint_at" type="datetime-local">
          </label>
          <button class="btn" type="submit">Add target</button>
        </form>
      </section>`;
  }
  if (state.view === "settings") {
    const settings = state.overview?.settings || {};
    const minGap = settings.availability_min_gap_seconds || 16;
    return `
      <section class="panel">
        <h2>Check pace</h2>
        <p class="help">The availability endpoint is documented at about 20 requests per 5 minutes per account. The per-account gap cannot go below ${minGap} seconds.</p>
        <form class="form-grid" data-action="save-settings" style="margin-top:14px">
          <label>Per-account gap (seconds)
            <input name="per_account_gap_seconds" type="number" min="${minGap}" max="3600" value="${esc(settings.per_account_gap_seconds || minGap)}" required>
          </label>
          <label>Slow gap (seconds)
            <input name="slow_gap_seconds" type="number" min="60" max="86400" value="${esc(settings.slow_gap_seconds || 600)}" required>
          </label>
          <label>Window before hint (hours)
            <input name="fast_window_before_hours" type="number" min="0" max="168" value="${esc(settings.fast_window_before_hours ?? 2)}" required>
          </label>
          <label>Window after hint (hours)
            <input name="fast_window_after_hours" type="number" min="0" max="168" value="${esc(settings.fast_window_after_hours ?? 6)}" required>
          </label>
          <button class="btn" type="submit">Save pace</button>
        </form>
      </section>`;
  }
  return "";
}

function liveHtml() {
  const overview = state.overview;
  if (!overview) return `<p class="empty">Loading the latest status…</p>`;
  if (state.view === "monitor") return monitorHtml(overview);
  if (state.view === "accounts") return accountsHtml(overview);
  if (state.view === "targets") return targetsHtml(overview);
  if (state.view === "activity") return activityHtml(overview);
  return limitsHtml(overview);
}

function monitorHtml(overview) {
  const accounts = overview.accounts.length;
  const ready = overview.accounts.filter((row) => row.enabled && (row.status === "ready" || row.status === "cooldown")).length;
  const targets = overview.targets.length;
  const claimed = overview.targets.filter((row) => row.state === "successful");
  return `
    <section class="stat-grid">
      <article class="stat"><span>Accounts</span><strong>${accounts}</strong></article>
      <article class="stat"><span>Able to check</span><strong>${ready}</strong></article>
      <article class="stat"><span>Targets</span><strong>${targets}</strong></article>
      <article class="stat"><span>Confirmed</span><strong>${claimed.length}</strong></article>
    </section>
    ${successPanel(claimed)}
    ${milestoneHtml(overview.milestone)}
    <section class="callout">
      <strong>How a claim happens.</strong>
      The monitor asks Mojang whether the name is AVAILABLE, then sends one username change on an account whose 30-day cooldown has cleared.
      Success is recorded only after a second profile read shows the new name.
      Each account waits at least ${esc(overview.settings.per_account_gap_seconds)} seconds between availability checks.
      At most 3 rename requests are sent per minute.
      For the first milestone, use a disposable username and press Check now on a target if you want one official check immediately.
    </section>
    ${targetsHtml(overview)}
    ${activityHtml(overview, 8)}`;
}

function successPanel(claimed) {
  if (!claimed.length) return "";
  const rows = claimed.map((target) => `
    <div class="success-row">
      <div>
        <div class="eyebrow">Confirmed claim</div>
        <strong class="name">${esc(target.username)}</strong>
        <div class="help">
          Profile read confirmed this name
          ${target.claimed_by_name ? " on " + esc(target.claimed_by_name) : ""}.
          ${target.claimed_at ? " " + fmt(target.claimed_at) : ""}
        </div>
      </div>
      ${pill("successful", "Successful")}
    </div>`).join("");
  return `<section class="success-panel">${rows}</section>`;
}

function milestoneHtml(milestone) {
  if (!milestone) return "";
  const items = milestone.items.map((item) => `
    <li class="${item.done ? "done" : "todo"}">
      <span class="mark">${item.done ? "✓" : "•"}</span>
      <div>
        <strong>${esc(item.label)}</strong>
        <div class="help">${esc(item.detail)}</div>
      </div>
    </li>`).join("");
  return `
    <section class="panel milestone ${milestone.complete ? "complete" : ""}">
      <div class="topbar" style="margin:0">
        <div>
          <div class="eyebrow">${esc(milestone.title)}</div>
          <h2>${milestone.complete ? "Ready for client sign-off" : "Demo checklist"}</h2>
          <p class="help">${esc(milestone.done_count)} / ${esc(milestone.total)} acceptance items. ${esc(milestone.next_step)}</p>
        </div>
        ${pill(milestone.complete ? "successful" : "checking", milestone.complete ? "Complete" : "In progress")}
      </div>
      <ol class="checklist">${items}</ol>
    </section>`;
}

function accountsHtml(overview) {
  const warning = overview.settings.microsoft_client_configured
    ? ""
    : `<section class="callout"><strong>Azure client id is missing.</strong> Set MICROSOFT_CLIENT_ID and restart before connecting an account. The steps are in README.md.</section>`;
  if (!overview.accounts.length) {
    return `${warning}<section class="panel empty">No accounts yet. Add one above. The cap for this server is ${esc(overview.settings.max_accounts)}.</section>`;
  }
  const rows = overview.accounts.map((account) => `
    <tr>
      <td>
        <strong>${esc(account.label)}</strong>
        <div class="help">${esc(account.mc_name || "No Minecraft profile yet")}</div>
        ${account.login ? loginBox(account) : ""}
      </td>
      <td>${pill(account.enabled ? account.status : "disabled", account.enabled ? labelStatus(account.status) : "Disabled")}</td>
      <td>${account.name_change_allowed == null ? "—" : account.name_change_allowed ? "Yes" : "No"}</td>
      <td>${esc(account.status_detail || "")}<div class="help">${fmt(account.last_checked_at)}</div></td>
      <td class="actions">
        <button class="btn quiet" type="button" data-action="refresh-account" data-id="${account.id}">Refresh</button>
        <button class="btn quiet" type="button" data-action="relogin" data-id="${account.id}">Reconnect</button>
        <button class="btn quiet" type="button" data-action="toggle-account" data-id="${account.id}" data-enabled="${account.enabled ? "0" : "1"}">${account.enabled ? "Disable" : "Enable"}</button>
        <button class="btn danger" type="button" data-action="delete-account" data-id="${account.id}">Remove</button>
      </td>
    </tr>`).join("");
  return `
    ${warning}
    <section class="panel">
      <table>
        <thead><tr><th>Account</th><th>Status</th><th>Can rename</th><th>Detail</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </section>`;
}

function loginBox(account) {
  const login = account.login;
  return `
    <div class="login-box">
      <div class="help">${esc(login.message || "Finish sign-in at Microsoft, then come back. This page updates on its own.")}</div>
      <div class="code">${esc(login.user_code)}</div>
      <div class="actions">
        <a class="btn" href="${esc(login.verification_uri)}" target="_blank" rel="noreferrer">Open Microsoft login</a>
        <button class="btn secondary" type="button" data-action="copy-code" data-code="${esc(login.user_code)}">Copy code</button>
      </div>
    </div>`;
}

function targetsHtml(overview) {
  if (!overview.targets.length) {
    return `<section class="panel empty">No target usernames yet.</section>`;
  }
  const rows = overview.targets.map((target) => `
    <tr>
      <td class="name">${esc(target.username)}</td>
      <td>${pill(target.priority, priorityLabel(target.priority))}</td>
      <td>${pill(target.state, labelStatus(target.state))}${target.enabled ? "" : " <span class='help'>paused</span>"}</td>
      <td>${esc(target.state_detail || "")}<div class="help">Checked ${fmt(target.last_checked_at)}${target.claimed_by_name ? " · " + esc(target.claimed_by_name) : ""}</div></td>
      <td>${target.release_hint_at ? fmt(target.release_hint_at) : "—"}</td>
      <td class="actions">
        <button class="btn quiet" type="button" data-action="check-target" data-id="${target.id}" ${target.state === "successful" || target.state === "attempting" ? "disabled" : ""}>Check now</button>
        <button class="btn quiet" type="button" data-action="toggle-target" data-id="${target.id}" data-enabled="${target.enabled ? "0" : "1"}">${target.enabled ? "Pause" : "Resume"}</button>
        <button class="btn danger" type="button" data-action="delete-target" data-id="${target.id}">Remove</button>
      </td>
    </tr>`).join("");
  return `
    <section class="panel">
      <table>
        <thead><tr><th>Name</th><th>Priority</th><th>State</th><th>Detail</th><th>Hint</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </section>`;
}

function activityHtml(overview, limit) {
  const rowsData = limit ? overview.activity.slice(0, limit) : overview.activity;
  if (!rowsData.length) return `<section class="panel empty">No checks yet. Start the monitor after an account is connected.</section>`;
  const rows = rowsData.map((row) => `
    <tr>
      <td>${fmt(row.timestamp)}</td>
      <td>${esc(row.action)}</td>
      <td>${pill(row.result, labelStatus(row.result))}</td>
      <td class="name">${esc(row.username || "—")}</td>
      <td>${esc(row.account_label || "—")}</td>
      <td>${row.http_status ?? "—"}</td>
      <td>${esc(row.detail || "")}</td>
    </tr>`).join("");
  return `
    <section class="panel">
      <h2>Recent activity</h2>
      <table>
        <thead><tr><th>When</th><th>Action</th><th>Result</th><th>Name</th><th>Account</th><th>HTTP</th><th>Detail</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </section>`;
}

function limitsHtml(overview) {
  return `
    <section class="callout">
      <strong>Current pace.</strong>
      Availability gap ${esc(overview.settings.per_account_gap_seconds)}s per account.
      Slow gap ${esc(overview.settings.slow_gap_seconds)}s when a release hint is outside the
      ${esc(overview.settings.fast_window_before_hours)}h / ${esc(overview.settings.fast_window_after_hours)}h window.
      Account cap ${esc(overview.settings.max_accounts)}.
      Mojang's published Java rule is that an old name becomes free about 37 days after a change; this monitor still waits for AVAILABLE.
    </section>
    ${jobsHtml(overview)}`;
}

function jobsHtml(overview) {
  if (!overview.jobs.length) return `<section class="panel empty">No claim jobs yet.</section>`;
  const rows = overview.jobs.map((job) => `
    <tr>
      <td class="name">${esc(job.username)}</td>
      <td>${pill(job.status, labelStatus(job.status))}</td>
      <td>${esc(job.attempt_count)}</td>
      <td>${fmt(job.started_at)}</td>
      <td>${esc(job.result_detail || "")}</td>
    </tr>`).join("");
  return `
    <section class="panel">
      <h2>Claim jobs</h2>
      <table>
        <thead><tr><th>Name</th><th>Status</th><th>Attempts</th><th>Started</th><th>Result</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </section>`;
}

function updateChrome() {
  const overview = state.overview;
  const lamp = document.getElementById("run-lamp");
  const label = document.getElementById("run-label");
  const banner = document.getElementById("banner");
  if (banner) setBanner(banner);
  if (!overview || !lamp || !label) return;
  const fresh = heartbeatFresh(overview.worker?.heartbeat);
  if (!fresh) {
    lamp.className = "lamp off";
    label.textContent = "Monitor is not heartbeating";
  } else if (overview.sniper_running) {
    lamp.className = "lamp on";
    label.textContent = "Running";
  } else {
    lamp.className = "lamp idle";
    label.textContent = "Stopped";
  }
  document.querySelectorAll("nav button").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === state.view);
  });
}

function setBanner(node) {
  node.classList.toggle("hidden", !state.banner);
  node.classList.toggle("ok", state.bannerOk);
  node.textContent = state.banner;
}

function heartbeatFresh(value) {
  if (!value) return false;
  const then = new Date(value).getTime();
  return Number.isFinite(then) && Date.now() - then < 25000;
}

async function onSubmit(event) {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !form.dataset.action) return;
  event.preventDefault();
  const data = Object.fromEntries(new FormData(form).entries());
  try {
    if (form.dataset.action === "login") {
      const payload = await api("/api/login", { method: "POST", body: data }, false);
      state.me = { csrf_token: payload.csrf_token };
      state.csrf = payload.csrf_token;
      state.banner = "";
      renderedView = null;
      await refresh();
      ensurePoll();
      return;
    }
    if (form.dataset.action === "add-account") {
      await api("/api/accounts", { method: "POST", body: { label: data.label } });
      form.reset();
      note("Microsoft sign-in started. Use the code on the account row.");
    } else if (form.dataset.action === "add-target") {
      await api("/api/targets", {
        method: "POST",
        body: {
          username: data.username,
          priority: data.priority,
          release_hint_at: data.release_hint_at ? new Date(data.release_hint_at).toISOString() : null,
        },
      });
      form.reset();
      note("Target added.");
    } else if (form.dataset.action === "save-settings") {
      await api("/api/settings", {
        method: "PUT",
        body: {
          per_account_gap_seconds: Number(data.per_account_gap_seconds),
          slow_gap_seconds: Number(data.slow_gap_seconds),
          fast_window_before_hours: Number(data.fast_window_before_hours),
          fast_window_after_hours: Number(data.fast_window_after_hours),
        },
      });
      note("Pace saved.");
    }
    await refresh();
  } catch (error) {
    fail(error.message);
  }
}

async function onClick(event) {
  const viewButton = event.target.closest("[data-view]");
  if (viewButton) {
    state.view = viewButton.dataset.view;
    state.banner = "";
    renderedView = null;
    paint();
    return;
  }
  const button = event.target.closest("[data-action]");
  if (!button || button.closest("form")) return;
  const id = button.dataset.id;
  try {
    if (button.dataset.action === "start") await api("/api/sniper/start", { method: "POST", body: {} });
    if (button.dataset.action === "stop") await api("/api/sniper/stop", { method: "POST", body: {} });
    if (button.dataset.action === "logout") {
      await api("/api/logout", { method: "POST", body: {} });
      state.me = null;
      state.overview = null;
      renderedView = null;
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
      paint();
      return;
    }
    if (button.dataset.action === "copy-code") {
      await navigator.clipboard.writeText(button.dataset.code);
      note("Code copied.");
    }
    if (button.dataset.action === "delete-account" && confirm("Remove this account and its saved session?")) {
      await api(`/api/accounts/${id}`, { method: "DELETE" });
    }
    if (button.dataset.action === "delete-target" && confirm("Remove this target?")) {
      await api(`/api/targets/${id}`, { method: "DELETE" });
    }
    if (button.dataset.action === "toggle-account") {
      await api(`/api/accounts/${id}/enabled`, { method: "POST", body: { enabled: button.dataset.enabled === "1" } });
    }
    if (button.dataset.action === "toggle-target") {
      await api(`/api/targets/${id}`, { method: "PATCH", body: { enabled: button.dataset.enabled === "1" } });
    }
    if (button.dataset.action === "refresh-account") {
      await api(`/api/accounts/${id}/refresh`, { method: "POST", body: {} });
      note("Account refreshed.");
    }
    if (button.dataset.action === "relogin") {
      await api(`/api/accounts/${id}/relogin`, { method: "POST", body: {} });
      note("A new Microsoft code is ready on that account.");
    }
    if (button.dataset.action === "check-target") {
      const result = await api(`/api/targets/${id}/check`, { method: "POST", body: {} });
      note(`Check result for ${result.username || "target"}: ${labelStatus(result.state)}. ${result.detail || ""}`.trim());
    }
    await refresh();
  } catch (error) {
    fail(error.message);
  }
}

async function api(path, options, authed = true) {
  const headers = { "Content-Type": "application/json" };
  if (authed) headers["X-CSRF-Token"] = state.csrf;
  const response = await fetch(path, {
    method: options.method,
    headers,
    body: options.body == null ? undefined : JSON.stringify(options.body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof payload.detail === "string" ? payload.detail : "Request failed.";
    throw new Error(detail);
  }
  return payload;
}

function ensurePoll() {
  if (pollTimer) return;
  pollTimer = setInterval(refresh, 2000);
}

function note(message) {
  state.banner = message;
  state.bannerOk = true;
  paint();
}

function fail(message) {
  state.banner = message;
  state.bannerOk = false;
  paint();
}

function pill(status, text) {
  return `<span class="pill ${STATUS_CLASS[status] || ""}">${esc(text)}</span>`;
}

function labelStatus(status) {
  if (STATUS_LABELS[status]) return STATUS_LABELS[status];
  return String(status || "unknown").replaceAll("_", " ");
}

function priorityLabel(priority) {
  if (priority === "3c") return "3-character";
  if (priority === "og") return "OG";
  return "Normal";
}

function fmt(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}
