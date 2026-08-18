"use strict";

/* ---------- state ---------- */
const LS = {
  token: "jarvis_token",
  theme: "jarvis_theme",
  gfx: "jarvis_gfx",
  panel: "jarvis_panel",
  history: "jarvis_cmd_history",
};

const state = {
  token: localStorage.getItem(LS.token) || "",
  theme: localStorage.getItem(LS.theme) || "aurora",
  gfx: localStorage.getItem(LS.gfx) || "lite",
  ws: null,
  wsRetry: 0,
  connected: false,
  feedCount: 0,
  voiceRunning: false,
  cmdHistory: [],
  historyIdx: -1,
};

const $ = (id) => document.getElementById(id);
const fmtTime = () => new Date().toLocaleTimeString("en-GB", { hour12: false });

/* ---------- api ---------- */
async function api(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  if (state.token) headers["Authorization"] = "Bearer " + state.token;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, Object.assign({}, opts, { headers }));
  if (res.status === 401) {
    showConnect("Session expired — re-enter your token.");
    return null;
  }
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))).detail || res.statusText;
    throw new Error(detail);
  }
  return res.json();
}

/* ---------- connect ---------- */
function showConnect(err) {
  $("overlay").classList.remove("hidden");
  if (err) $("connect-err").textContent = err;
}

$("connect-btn").addEventListener("click", connect);
$("token-input").addEventListener("keydown", (e) => { if (e.key === "Enter") connect(); });
$("logout-btn").addEventListener("click", () => {
  state.token = "";
  localStorage.removeItem(LS.token);
  if (state.ws) state.ws.close();
  showConnect();
});

async function connect() {
  $("connect-err").textContent = "";
  const token = $("token-input").value.trim();
  if (token) {
    state.token = token;
    localStorage.setItem(LS.token, token);
  }
  try {
    const h = await api("/v1/health");
    $("brand-sub").textContent = (h.provider || "engine") + " · " + (h.app || "JARVIS");
    $("voice-pill").classList.toggle("on", !!h.voice_running);
    state.voiceRunning = !!h.voice_running;
    $("overlay").classList.add("hidden");
    openSocket();
  } catch (e) {
    $("connect-err").textContent = "Could not reach engine: " + e.message;
  }
}

/* ---------- websocket ---------- */
function wsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const q = state.token ? "?token=" + encodeURIComponent(state.token) : "";
  return proto + "//" + location.host + "/ws/events" + q;
}

function openSocket() {
  if (state.ws) return;
  const ws = new WebSocket(wsUrl());
  state.ws = ws;
  ws.onopen = () => {
    state.connected = true;
    state.wsRetry = 0;
    $("conn-pill").textContent = "LIVE";
    $("conn-pill").className = "pill on";
  };
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    handleEvent(msg.topic, msg.payload || {});
  };
  ws.onclose = () => {
    state.ws = null;
    state.connected = false;
    $("conn-pill").textContent = "OFFLINE";
    $("conn-pill").className = "pill off";
    if (!$("overlay").classList.contains("hidden")) return;
    const delay = Math.min(1000 * 2 ** state.wsRetry++, 15000);
    setTimeout(openSocket, delay);
  };
  ws.onerror = () => ws.close();
}

function handleEvent(topic, payload) {
  switch (topic) {
    case "command_start":
      feed("start", payload.command, "running…", true);
      break;
    case "command_done":
      feed(payload.status === "failed" ? "fail" : "done", payload.command, payload.response_text || "", false);
      break;
    case "agent_step": {
      const title = "agent " + (payload.iteration != null ? "#" + payload.iteration + " " : "") + (payload.tool || "step");
      const body = (payload.status || "") + (payload.error ? " · " + payload.error : "") + (payload.output_snippet ? " · " + payload.output_snippet : "");
      feed("step", title, body, false);
      break;
    }
    case "scheduler_run":
      feed(payload.status === "failed" ? "fail" : "info", "scheduled: " + (payload.command || ""), payload.result || payload.status, false);
      break;
    case "approval":
      pollApprovals();
      break;
  }
}

/* ---------- feed ---------- */
function feed(tag, title, body, pending) {
  const box = $("feed");
  const row = document.createElement("div");
  row.className = "frow";
  const t = document.createElement("span");
  t.className = "t";
  t.textContent = fmtTime();
  const tagEl = document.createElement("span");
  tagEl.className = "tag " + tag;
  tagEl.textContent = tag.toUpperCase();
  const txt = document.createElement("span");
  txt.className = "txt" + (pending ? " pending" : "");
  txt.textContent = (title ? title + " — " : "") + body;
  row.appendChild(t); row.appendChild(tagEl); row.appendChild(txt);
  box.appendChild(row);
  if (++state.feedCount > 200) box.removeChild(box.firstChild);
  if (box.scrollTop + box.clientHeight >= box.scrollHeight - 40 || pending) {
    box.scrollTop = box.scrollHeight;
  }
}

/* ---------- execute ---------- */
async function runCommand(cmd) {
  cmd = cmd.trim();
  if (!cmd) return;
  if (state.cmdHistory[state.cmdHistory.length - 1] !== cmd) {
    state.cmdHistory.push(cmd);
    if (state.cmdHistory.length > 50) state.cmdHistory.shift();
    localStorage.setItem(LS.history, JSON.stringify(state.cmdHistory));
  }
  $("cmd-input").value = "";
  state.historyIdx = -1;
  if (!state.connected) feed("warn", cmd, "not connected — attempting direct call…", false);
  try {
    await api("/v1/execute", { method: "POST", body: JSON.stringify({ command: cmd, source: "web", auto_approve: false }) });
  } catch (e) {
    feed("fail", cmd, e.message, false);
  }
}

function executeCurrent() { runCommand($("cmd-input").value); }
$("cmd-run").addEventListener("click", executeCurrent);
$("cmd-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") return executeCurrent();
  if (e.key === "ArrowUp") {
    e.preventDefault();
    if (state.historyIdx < 0) state.historyIdx = state.cmdHistory.length;
    if (state.historyIdx > 0) {
      state.historyIdx--;
      $("cmd-input").value = state.cmdHistory[state.historyIdx] || "";
    }
  } else if (e.key === "ArrowDown") {
    e.preventDefault();
    if (state.historyIdx < state.cmdHistory.length - 1) {
      state.historyIdx++;
      $("cmd-input").value = state.cmdHistory[state.historyIdx] || "";
    } else {
      state.historyIdx = state.cmdHistory.length;
      $("cmd-input").value = "";
    }
  }
});
document.querySelectorAll(".chip").forEach((chip) => {
  chip.addEventListener("click", () => { $("cmd-input").value = chip.dataset.cmd; $("cmd-input").focus(); });
});

/* ---------- navigation ---------- */
document.querySelectorAll(".nav-btn[data-panel]").forEach((btn) => {
  btn.addEventListener("click", () => switchPanel(btn.dataset.panel));
});
function switchPanel(name) {
  document.querySelectorAll(".nav-btn[data-panel]").forEach((b) => b.classList.toggle("active", b.dataset.panel === name));
  document.querySelectorAll(".panel").forEach((p) => { p.hidden = p.id !== "panel-" + name; });
  localStorage.setItem(LS.panel, name);
}

/* ---------- resources ---------- */
let resHistory = { cpu: [], ram: [] };
async function pollResources() {
  try {
    const r = await api("/v1/resources");
    const gpu = r.gpu || {};
    setMeter("m-cpu", r.cpu_percent, "cpu-val", r.cpu_percent + "%");
    setMeter("m-gpu", gpu.utilization ?? 0, "gpu-val", (gpu.utilization ?? "—") + "%");
    setMeter("m-ram", r.memory_percent, "ram-val", r.memory_percent + "%");
    setMeter("m-disk", r.disk_percent, "disk-val", r.disk_percent + "%");
    if (r.cpu_history && r.cpu_history.length) resHistory.cpu = r.cpu_history;
    if (r.memory_history && r.memory_history.length) resHistory.ram = r.memory_history;
    drawSpark("spark-cpu", resHistory.cpu);
    drawSpark("spark-ram", resHistory.ram);
  } catch (e) { /* ignore transient */ }
}
function setMeter(id, pct, valId, text) {
  const el = $(id);
  if (!el) return;
  el.style.setProperty("--p", Math.max(0, Math.min(100, pct)));
  $(valId).textContent = text;
}
function drawSpark(canvasId, data) {
  const cv = $(canvasId);
  if (!cv) return;
  const ctx = cv.getContext("2d");
  const w = cv.width, h = cv.height;
  ctx.clearRect(0, 0, w, h);
  if (!data || !data.length) return;
  const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
  ctx.strokeStyle = accent;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  const max = 100, step = w / (data.length - 1 || 1);
  data.forEach((v, i) => {
    const y = h - (Math.min(100, Math.max(0, v)) / max) * (h - 2) - 1;
    if (i === 0) ctx.moveTo(0, y); else ctx.lineTo(i * step, y);
  });
  ctx.stroke();
}

/* ---------- approvals ---------- */
async function pollApprovals() {
  try {
    const list = await api("/v1/pending-approvals");
    const box = $("approval-list");
    const badge = $("approval-badge");
    badge.hidden = list.length === 0;
    badge.textContent = list.length;
    $("approval-sub").textContent = list.length ? list.length + " pending" : "no pending approvals";
    box.innerHTML = "";
    list.forEach((a) => {
      const card = document.createElement("div");
      card.className = "acard glass";
      const risk = document.createElement("span");
      risk.className = "risk " + (a.risk || "medium");
      risk.textContent = (a.risk || "medium").toUpperCase();
      const cmd = document.createElement("div");
      cmd.className = "cmd";
      const b = document.createElement("b"); b.textContent = a.command || "(command)";
      const s = document.createElement("span"); s.className = "sub"; s.textContent = a.tool + " · " + (a.reason || "needs approval");
      cmd.appendChild(b); cmd.appendChild(s);
      const acts = document.createElement("div");
      acts.className = "acts";
      const appr = document.createElement("button");
      appr.className = "btn btn-primary"; appr.textContent = "APPROVE";
      appr.onclick = async () => { try { await api("/v1/approve/" + a.approval_id, { method: "POST" }); pollApprovals(); } catch (e) { feed("fail", "approve", e.message, false); } };
      const rej = document.createElement("button");
      rej.className = "btn btn-danger"; rej.textContent = "REJECT";
      rej.onclick = async () => { try { await api("/v1/reject/" + a.approval_id, { method: "POST" }); pollApprovals(); } catch (e) { feed("fail", "reject", e.message, false); } };
      acts.appendChild(appr); acts.appendChild(rej);
      card.appendChild(risk); card.appendChild(cmd); card.appendChild(acts);
      box.appendChild(card);
    });
  } catch (e) { /* ignore */ }
}

/* ---------- jobs ---------- */
async function pollJobs() {
  try {
    const task = await api("/v1/job/task");
    renderJobTask(task);
  } catch (e) { /* ignore */ }
  try {
    const applied = await api("/v1/job/applied?limit=12");
    const totals = applied.totals || {};
    $("stat-applied").textContent = totals.applied || 0;
    $("stat-interview").textContent = totals.interview || 0;
    $("stat-offer").textContent = totals.offer || 0;
    $("stat-rejected").textContent = totals.rejected || 0;
    renderJobFeed(applied.items || []);
  } catch (e) { /* ignore */ }
}
function renderJobTask(task) {
  const box = $("job-task");
  if (!task || !task.query) {
    box.innerHTML = '<div class="task-row"><span class="sub">No active job task</span></div>';
    $("job-resume-btn").hidden = true;
    $("job-cancel-btn").hidden = true;
    return;
  }
  const phase = task.phase || (task.status || "idle");
  const applied = task.applied != null ? task.applied : 0;
  const skipped = task.skipped != null ? task.skipped : 0;
  const failed = task.failed != null ? task.failed : 0;
  const max = task.max_applications || 0;
  const pct = max ? Math.round((applied / max) * 100) : 0;
  const pause = task.paused_reason;
  const progLine = (task.progress && task.progress.length) ? " · " + esc(String(task.progress[task.progress.length - 1])) : "";
  box.innerHTML =
    '<div class="task-row"><span class="sub">query</span> <b>' + esc(task.query) + "</b></div>" +
    '<div class="task-row"><span class="sub">status</span> ' + esc(String(phase).toUpperCase()) +
    (pause ? " <span class='pill wait'>" + esc(pause) + "</span>" : "") + progLine + "</div>" +
    '<div class="task-row"><span class="sub">counters</span> applied ' + applied + " · skipped " + skipped + " · failed " + failed + "</div>" +
    '<div class="prog"><div style="width:' + Math.max(0, Math.min(100, pct)) + '%"></div></div>';
  $("job-resume-btn").hidden = task.status !== "paused";
  $("job-cancel-btn").hidden = task.status !== "running" && task.status !== "paused";
}
function renderJobFeed(items) {
  const box = $("job-feed");
  box.innerHTML = "";
  if (!items.length) { box.innerHTML = '<span class="sub">No applications yet.</span>'; return; }
  items.forEach((j) => {
    const row = document.createElement("div");
    row.className = "frow";
    const tag = document.createElement("span");
    tag.className = "tag " + (j.status === "applied" ? "done" : j.status === "failed" ? "fail" : "info");
    tag.textContent = (j.status || "?").toUpperCase();
    const txt = document.createElement("span");
    txt.className = "txt";
    txt.textContent = (j.title || "") + " @ " + (j.company || j.site || "") + " — " + (j.created_at || "").slice(0, 16).replace("T", " ");
    row.appendChild(tag); row.appendChild(txt);
    box.appendChild(row);
  });
}
function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

$("job-apply-btn").addEventListener("click", async () => {
  const q = $("job-query").value.trim();
  if (!q) return;
  try { await api("/v1/job/apply", { method: "POST", body: JSON.stringify({ query: q }) }); pollJobs(); } catch (e) { feed("fail", "apply", e.message, false); }
});
$("job-resume-btn").addEventListener("click", async () => { try { await api("/v1/job/resume", { method: "POST" }); pollJobs(); } catch (e) { feed("fail", "resume", e.message, false); } });
$("job-cancel-btn").addEventListener("click", async () => { try { await api("/v1/job/cancel", { method: "POST" }); pollJobs(); } catch (e) { feed("fail", "cancel", e.message, false); } });

/* ---------- scheduler ---------- */
async function pollSchedules() {
  try {
    const list = await api("/v1/schedules");
    const box = $("sched-feed");
    box.innerHTML = "";
    if (!list.length) { box.innerHTML = '<span class="sub">No schedules yet. Use <b>schedule &lt;command&gt; daily at HH:MM</b> (hourly, every N minutes, weekly on &lt;day&gt; at HH:MM).</span>'; return; }
    list.forEach((s) => {
      const row = document.createElement("div");
      row.className = "frow";
      const tag = document.createElement("span");
      tag.className = "tag " + (s.last_status === "failed" ? "fail" : s.last_status ? "done" : "info");
      tag.textContent = (s.last_status || "idle").toUpperCase();
      const txt = document.createElement("span");
      txt.className = "txt";
      txt.textContent = "[" + s.id + "] " + s.command + " — " + s.recurrence + (s.last_result ? " · last: " + s.last_result : "");
      row.appendChild(tag); row.appendChild(txt);
      const cancel = document.createElement("button");
      cancel.className = "btn btn-danger";
      cancel.style.padding = "3px 10px";
      cancel.textContent = "✕";
      cancel.onclick = async () => { try { await api("/v1/schedules/" + s.id, { method: "DELETE" }); pollSchedules(); } catch (e) { feed("fail", "cancel schedule", e.message, false); } };
      row.appendChild(cancel);
      box.appendChild(row);
    });
  } catch (e) { /* ignore */ }
}
$("sched-add-btn").addEventListener("click", addSchedule);
$("sched-rec").addEventListener("keydown", (e) => { if (e.key === "Enter") addSchedule(); });
async function addSchedule() {
  const cmd = $("sched-cmd").value.trim();
  const rec = $("sched-rec").value.trim();
  if (!cmd || !rec) return;
  try {
    await api("/v1/schedules", { method: "POST", body: JSON.stringify({ command: cmd, recurrence: rec }) });
    $("sched-cmd").value = ""; $("sched-rec").value = "";
    pollSchedules();
  } catch (e) { feed("fail", "schedule", e.message, false); }
}

/* ---------- voice ---------- */
$("voice-toggle").addEventListener("click", async () => {
  try {
    if (state.voiceRunning) { await api("/v1/voice/stop", { method: "POST" }); state.voiceRunning = false; }
    else { await api("/v1/voice/start", { method: "POST" }); state.voiceRunning = true; }
    updateVoiceUI();
  } catch (e) { feed("fail", "voice", e.message, false); }
});
function updateVoiceUI() {
  const st = state.voiceRunning ? "RUNNING" : "IDLE";
  $("voice-state").textContent = st;
  $("voice-pill").textContent = "VOICE " + st;
  $("voice-pill").className = "pill " + (state.voiceRunning ? "on" : "");
  $("voice-toggle").textContent = state.voiceRunning ? "STOP VOICE" : "START VOICE";
}
async function pollVoice() {
  try {
    const h = await api("/v1/health");
    state.voiceRunning = !!h.voice_running;
    updateVoiceUI();
  } catch (e) { /* ignore */ }
}

/* ---------- background canvas ---------- */
const bg = $("bg");
const bgCtx = bg.getContext("2d");
let particles = [];
function initBg() {
  const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
  bg.width = innerWidth * dpr;
  bg.height = innerHeight * dpr;
  bgCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const n = state.gfx === "full" ? 55 : 16;
  particles = Array.from({ length: n }, () => ({
    x: Math.random() * innerWidth,
    y: Math.random() * innerHeight,
    r: Math.random() * 1.8 + 0.4,
    vx: (Math.random() - 0.5) * (state.gfx === "full" ? 0.35 : 0.15),
    vy: (Math.random() - 0.5) * (state.gfx === "full" ? 0.35 : 0.15),
    a: Math.random() * 0.5 + 0.15,
  }));
}
let lastFrame = 0;
function drawBg(ts) {
  if (document.visibilityState !== "visible") return;
  if (ts - lastFrame < 33) { requestAnimationFrame(drawBg); return; } /* ~30fps */
  lastFrame = ts;
  const w = innerWidth, h = innerHeight;
  const css = getComputedStyle(document.documentElement);
  const accent = css.getPropertyValue("--accent").trim();
  const accent2 = css.getPropertyValue("--accent2").trim();
  bgCtx.clearRect(0, 0, w, h);
  if (state.gfx === "full") {
    const t = ts / 6000;
    for (let i = 0; i < 3; i++) {
      const x = w * (0.2 + 0.6 * (0.5 + 0.5 * Math.sin(t + i * 2.1)));
      const y = h * (0.2 + 0.6 * (0.5 + 0.5 * Math.cos(t * 0.8 + i * 1.7)));
      const r = Math.max(w, h) * (0.4 + i * 0.12);
      const g = bgCtx.createRadialGradient(x, y, 0, x, y, r);
      g.addColorStop(0, hexToRgba(i === 0 ? accent : accent2, 0.10));
      g.addColorStop(1, "rgba(0,0,0,0)");
      bgCtx.fillStyle = g;
      bgCtx.fillRect(0, 0, w, h);
    }
  } else {
    const g = bgCtx.createLinearGradient(0, 0, w, h);
    g.addColorStop(0, hexToRgba(accent, 0.05));
    g.addColorStop(1, hexToRgba(accent2, 0.05));
    bgCtx.fillStyle = g;
    bgCtx.fillRect(0, 0, w, h);
  }
  for (const p of particles) {
    p.x += p.vx; p.y += p.vy;
    if (p.x < 0) p.x = w; if (p.x > w) p.x = 0;
    if (p.y < 0) p.y = h; if (p.y > h) p.y = 0;
    bgCtx.beginPath();
    bgCtx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
    bgCtx.fillStyle = "rgba(148, 163, 222, " + p.a + ")";
    bgCtx.fill();
  }
  requestAnimationFrame(drawBg);
}
function hexToRgba(hex, a) {
  const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  if (!m) return "rgba(100,120,180," + a + ")";
  return "rgba(" + parseInt(m[1], 16) + "," + parseInt(m[2], 16) + "," + parseInt(m[3], 16) + "," + a + ")";
}

/* ---------- voice waveform ---------- */
const wave = $("wave");
const wctx = wave.getContext("2d");
function drawWave() {
  const css = getComputedStyle(document.documentElement);
  const accent = css.getPropertyValue("--accent").trim();
  const w = wave.width, h = wave.height;
  wctx.clearRect(0, 0, w, h);
  const amp = state.voiceRunning ? 26 : 6;
  const bars = 72;
  wctx.strokeStyle = accent;
  wctx.lineWidth = 2;
  wctx.beginPath();
  const mid = h / 2;
  for (let i = 0; i <= bars; i++) {
    const x = (i / bars) * w;
    const phase = i * 0.35;
    const env = Math.exp(-Math.pow((i - bars / 2) / (bars / 2.6), 2));
    let y = mid;
    if (state.voiceRunning) {
      y = mid + Math.sin(performance.now() / 220 + phase) * amp * env;
    }
    if (i === 0) wctx.moveTo(x, y); else wctx.lineTo(x, y);
  }
  wctx.stroke();
}

/* ---------- themes & gfx ---------- */
function applyTheme() {
  document.documentElement.setAttribute("data-theme", state.theme);
}
$("theme-btn").addEventListener("click", () => {
  const order = ["aurora", "cyber", "light"];
  state.theme = order[(order.indexOf(state.theme) + 1) % order.length];
  localStorage.setItem(LS.theme, state.theme);
  applyTheme();
});
$("gfx-btn").addEventListener("click", () => {
  state.gfx = state.gfx === "full" ? "lite" : "full";
  localStorage.setItem(LS.gfx, state.gfx);
  initBg();
  feed("info", "graphics", state.gfx === "full" ? "full effects" : "lite (battery-friendly)", false);
});

/* ---------- boot ---------- */
function boot() {
  applyTheme();
  initBg();
  requestAnimationFrame(drawBg);
  setInterval(drawWave, 60);
  const savedHistory = localStorage.getItem(LS.history);
  if (savedHistory) try { state.cmdHistory = JSON.parse(savedHistory); } catch { state.cmdHistory = []; }
  const savedPanel = localStorage.getItem(LS.panel);
  if (savedPanel) switchPanel(savedPanel);
  if (state.token) connect(); else showConnect();
  setInterval(() => { if (document.visibilityState === "visible") pollResources(); }, 2000);
  setInterval(pollApprovals, 3000);
  setInterval(pollJobs, 4000);
  setInterval(pollSchedules, 10000);
  setInterval(pollVoice, 5000);
  window.addEventListener("resize", initBg);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") { pollResources(); pollApprovals(); } });
}
boot();