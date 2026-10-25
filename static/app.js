const $ = (id) => document.getElementById(id);
const state = {
  mode: "AI",
  activeMission: null,
  controlSocket: null,
  eventSocket: null,
  pressed: new Set(),
  wheelUp: 0,
  wheelTimer: null,
};

const statusNames = {
  DRAFTING: "正在生成计划",
  AWAITING_APPROVAL: "等待任务确认",
  EXECUTING: "正在执行",
  AWAITING_STEP_APPROVAL: "等待高风险步骤确认",
  COMPLETED: "任务完成",
  FAILED: "任务失败",
  CANCELLED: "任务已取消",
  TAKEN_OVER: "人工已接管",
};

const authorityNames = { IDLE: "待命", MANUAL: "人工控制", AGENT: "AI 任务", SAFETY: "安全锁定" };

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const raw = await response.text();
  let data = {};
  try { data = raw ? JSON.parse(raw) : {}; } catch { data = { error: raw }; }
  if (!response.ok) throw new Error(data.error || data.detail || `请求失败 (${response.status})`);
  return data;
}

function setBusy(button, busy, label) {
  if (!button.dataset.label) {
    button.dataset.label = button.textContent.trim();
    button.dataset.originalHtml = button.innerHTML;
  }
  button.disabled = busy;
  button.setAttribute("aria-busy", String(busy));
  if (busy) button.textContent = label;
  else button.innerHTML = button.dataset.originalHtml || button.dataset.label;
}

function setMissionStatus(message, tone = "default") {
  const status = $("missionStatus");
  status.replaceChildren(create("span"), document.createTextNode(message));
  status.dataset.tone = tone;
}

function notify(message, tone = "default") {
  const toast = create("div", `toast ${tone === "default" ? "" : tone}`.trim(), message);
  $("toastRegion").append(toast);
  window.setTimeout(() => toast.remove(), 4200);
}

function showError(message) {
  setMissionStatus(message, "error");
  notify(message, "error");
}

async function refreshControl() {
  try {
    const control = await api("/api/control/status");
    const rail = $("authorityRail");
    rail.dataset.authority = control.authority;
    rail.setAttribute("aria-label", `当前控制权：${authorityNames[control.authority] || control.authority}`);
    $("authorityName").textContent = authorityNames[control.authority] || control.authority;
    $("authorityReason").textContent = control.reason || "控制权状态已更新";
    const executing = state.activeMission?.status === "EXECUTING" || state.activeMission?.status === "AWAITING_STEP_APPROVAL";
    $("takeoverBtn").classList.toggle("hidden", !executing || control.authority === "MANUAL");
  } catch (error) {
    $("authorityRail").dataset.authority = "SAFETY";
    $("authorityReason").textContent = error.message;
  }
}

function create(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderMission(mission) {
  state.activeMission = mission;
  const view = $("missionView");
  view.replaceChildren();
  setMissionStatus(statusNames[mission.status] || mission.status, mission.status === "FAILED" ? "error" : "default");
  if (!mission.plan) {
    const empty = create("div", "empty-state");
    empty.append(create("b", "", "任务计划不可用"), create("span", "", mission.error || "请重新生成任务。"));
    view.append(empty);
    return;
  }

  const completedSteps = mission.plan.steps.filter((step) => step.status === "COMPLETED").length;
  const totalSteps = mission.plan.steps.length;
  const progress = totalSteps ? Math.round((completedSteps / totalSteps) * 100) : 0;
  const summary = create("article", "mission-summary");
  const summaryTop = create("div", "mission-summary-top");
  const summaryCopy = create("div");
  summaryCopy.append(create("h3", "", mission.plan.summary), create("p", "", `计划版本 ${mission.revision} · ${totalSteps} 个步骤`));
  summaryTop.append(summaryCopy, create("span", "mission-progress-label", `${completedSteps} / ${totalSteps}`));
  const progressTrack = create("div", "mission-progress");
  const progressBar = create("i");
  progressBar.style.transform = `scaleX(${progress / 100})`;
  progressTrack.setAttribute("role", "progressbar");
  progressTrack.setAttribute("aria-valuemin", "0");
  progressTrack.setAttribute("aria-valuemax", "100");
  progressTrack.setAttribute("aria-valuenow", String(progress));
  progressTrack.append(progressBar);
  summary.append(summaryTop, progressTrack);
  view.append(summary);

  const list = create("div", "step-list");
  mission.plan.steps.forEach((step, index) => {
    const row = create("article", "step");
    row.dataset.status = step.status;
    row.append(create("span", "step-index", String(index + 1).padStart(2, "0")));
    const copy = create("div");
    copy.append(create("h4", "", step.title), create("p", "", step.expected_result));
    const risk = create("span", `risk ${step.risk_level}`, step.risk_level === "high" ? "需复核" : step.risk_level);
    row.append(copy, risk);
    list.append(row);
  });
  view.append(list);

  if (mission.error) view.append(create("div", "mission-error", mission.error));
  const actions = create("div", "mission-actions");
  if (mission.status === "AWAITING_APPROVAL") {
    const approve = create("button", "button primary", "批准并执行");
    approve.onclick = () => approveMission(approve);
    const revise = create("button", "button", "追加要求");
    revise.onclick = () => renderRevisionBox(view, actions);
    const reject = create("button", "button danger", "拒绝任务");
    reject.onclick = () => cancelMission(reject);
    actions.append(approve, revise, reject);
  } else if (mission.status === "AWAITING_STEP_APPROVAL") {
    const step = mission.plan.steps[mission.next_step_index];
    const approve = create("button", "button primary", `批准：${step?.title || "当前步骤"}`);
    approve.onclick = () => approveMission(approve);
    const reject = create("button", "button danger", "拒绝并结束");
    reject.onclick = () => cancelMission(reject);
    actions.append(approve, reject);
  } else if (mission.status === "EXECUTING") {
    const takeover = create("button", "button danger", "立即接管");
    takeover.onclick = () => enterManual(takeover);
    const cancel = create("button", "button", "取消并悬停");
    cancel.onclick = () => cancelMission(cancel);
    actions.append(takeover, cancel);
  }
  if (actions.children.length) view.append(actions);
  highlightMissionChip();
  refreshControl();
}

function renderRevisionBox(view, actions) {
  if (view.querySelector(".revision-box")) return;
  const box = create("div", "revision-box");
  const input = create("textarea", "revision-input");
  input.placeholder = "例如：最高飞行高度不超过 6 米，并在到达前先悬停确认。";
  const submit = create("button", "button primary", "按要求重新规划");
  submit.onclick = async () => {
    const requirements = input.value.trim();
    if (!requirements) return;
    setBusy(submit, true, "重新规划中…");
    try {
      const mission = await api(`/api/missions/${state.activeMission.mission_id}/revise`, {
        method: "POST",
        body: JSON.stringify({ requirements, model: $("modelSelect").value, base_url: $("urlSelect").value }),
      });
      renderMission(mission);
      notify("已按附加要求生成新版本计划", "success");
      await loadMission(mission.mission_id);
    } catch (error) { showError(error.message); setBusy(submit, false); }
  };
  box.append(input, submit);
  actions.after(box);
  input.focus();
}

async function createMission() {
  const prompt = $("missionInput").value.trim();
  if (!prompt) return;
  const button = $("planBtn");
  setBusy(button, true, "规划中…");
  setMissionStatus("正在生成任务概要");
  try {
    const mission = await api("/api/missions", {
      method: "POST",
      body: JSON.stringify({ prompt, model: $("modelSelect").value, base_url: $("urlSelect").value }),
    });
    $("missionInput").value = "";
    renderMission(mission);
    notify("任务概要已生成，请检查后确认", "success");
    await loadMissions();
  } catch (error) { showError(error.message); }
  finally { setBusy(button, false); }
}

async function approveMission(button) {
  setBusy(button, true, "提交审批…");
  try {
    await api(`/api/missions/${state.activeMission.mission_id}/approve`, { method: "POST", body: "{}" });
    notify("审批已提交，系统将按计划继续", "success");
    setTimeout(() => loadMission(state.activeMission.mission_id), 250);
  } catch (error) { showError(error.message); setBusy(button, false); }
}

async function cancelMission(button) {
  setBusy(button, true, "正在取消…");
  try {
    const mission = await api(`/api/missions/${state.activeMission.mission_id}/cancel`, { method: "POST", body: "{}" });
    renderMission(mission);
    notify("任务已取消，无人机将保持安全状态");
  } catch (error) { showError(error.message); setBusy(button, false); }
}

async function loadMission(id) {
  try { renderMission(await api(`/api/missions/${id}`)); } catch (error) { showError(error.message); }
}

async function loadMissions() {
  try {
    const { missions } = await api("/api/missions?limit=15");
    const list = $("missionList");
    list.replaceChildren();
    missions.forEach((mission) => {
      const button = create("button", "mission-chip", mission.plan?.summary || mission.prompt);
      button.dataset.id = mission.mission_id;
      button.title = `${statusNames[mission.status] || mission.status} · ${mission.prompt}`;
      button.onclick = () => loadMission(mission.mission_id);
      list.append(button);
    });
    highlightMissionChip();
  } catch (error) { showError(error.message); }
}

function highlightMissionChip() {
  document.querySelectorAll(".mission-chip").forEach((item) => item.classList.toggle("active", item.dataset.id === state.activeMission?.mission_id));
}

async function enterManual(button = $("manualModeBtn")) {
  setBusy(button, true, "接管中…");
  try {
    await api("/api/control/takeover", { method: "POST", body: "{}" });
    state.mode = "MANUAL";
    $("manualModeBtn").classList.add("active");
    $("aiModeBtn").classList.remove("active");
    $("manualModeBtn").setAttribute("aria-pressed", "true");
    $("aiModeBtn").setAttribute("aria-pressed", "false");
    $("manualOverlay").classList.remove("hidden");
    $("takeoffBtn").disabled = false;
    $("landBtn").disabled = false;
    connectControlSocket();
    await refreshControl();
    notify("人工控制权已取得，请点击飞行画面启用键盘", "success");
    if (state.activeMission) await loadMission(state.activeMission.mission_id);
  } catch (error) { showError(`接管失败：${error.message}`); }
  finally { setBusy(button, false); }
}

function leaveManual() {
  state.mode = "AI";
  state.pressed.clear();
  if (state.controlSocket?.readyState === WebSocket.OPEN) state.controlSocket.send(JSON.stringify({ type: "release" }));
  state.controlSocket?.close();
  state.controlSocket = null;
  $("manualModeBtn").classList.remove("active");
  $("aiModeBtn").classList.add("active");
  $("manualModeBtn").setAttribute("aria-pressed", "false");
  $("aiModeBtn").setAttribute("aria-pressed", "true");
  $("manualOverlay").classList.add("hidden");
  $("takeoffBtn").disabled = true;
  $("landBtn").disabled = true;
  $("controlLink").classList.remove("online");
  $("controlLink").textContent = "控制链路未启用";
  setTimeout(refreshControl, 200);
}

function socketUrl(path) {
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`;
}

function connectControlSocket() {
  if (state.controlSocket?.readyState === WebSocket.OPEN) return;
  const socket = new WebSocket(socketUrl("/ws/control"));
  state.controlSocket = socket;
  socket.onopen = () => { $("controlLink").classList.add("online"); $("controlLink").textContent = "20 Hz 控制链路已连接"; };
  socket.onmessage = () => {};
  socket.onclose = () => { $("controlLink").classList.remove("online"); $("controlLink").textContent = "控制链路已断开 · 无人机悬停"; };
  socket.onerror = () => showError("人工控制 WebSocket 连接失败");
}

function connectEventSocket() {
  const socket = new WebSocket(socketUrl("/ws/events"));
  state.eventSocket = socket;
  socket.onmessage = (event) => {
    const payload = JSON.parse(event.data);
    if (payload.mission && (!state.activeMission || payload.mission_id === state.activeMission.mission_id)) renderMission(payload.mission);
    if (payload.type === "control_changed") refreshControl();
    loadMissions();
  };
  socket.onclose = () => setTimeout(connectEventSocket, 1500);
}

function manualVector() {
  return {
    forward: (state.pressed.has("KeyW") ? 1 : 0) - (state.pressed.has("KeyS") ? 1 : 0),
    right: (state.pressed.has("KeyD") ? 1 : 0) - (state.pressed.has("KeyA") ? 1 : 0),
    up: state.wheelUp,
  };
}

function vectorActive(vector) { return vector.forward !== 0 || vector.right !== 0 || vector.up !== 0; }

setInterval(() => {
  if (state.mode !== "MANUAL" || document.activeElement !== $("videoStage")) return;
  const vector = manualVector();
  if (vectorActive(vector) && state.controlSocket?.readyState === WebSocket.OPEN) {
    state.controlSocket.send(JSON.stringify({ type: "velocity", ...vector }));
  }
}, 50);

async function requestHold() {
  try {
    if (state.mode !== "MANUAL") await enterManual($("holdBtn"));
    await api("/api/control/hold", { method: "POST", body: "{}" });
  } catch (error) { showError(`悬停失败：${error.message}`); }
}

$("videoStage").addEventListener("focus", () => { $("videoStage").dataset.focused = "true"; $("controlFocus").textContent = "键盘控制已启用"; });
$("videoStage").addEventListener("blur", () => { delete $("videoStage").dataset.focused; state.pressed.clear(); $("controlFocus").textContent = "点击画面启用键盘控制"; if (state.mode === "MANUAL") requestHold(); });
window.addEventListener("keydown", (event) => {
  if (state.mode !== "MANUAL" || document.activeElement !== $("videoStage")) return;
  if (["KeyW", "KeyA", "KeyS", "KeyD"].includes(event.code)) { event.preventDefault(); state.pressed.add(event.code); }
});
window.addEventListener("keyup", (event) => {
  if (!["KeyW", "KeyA", "KeyS", "KeyD"].includes(event.code)) return;
  state.pressed.delete(event.code);
  if (!vectorActive(manualVector()) && state.mode === "MANUAL") requestHold();
});
$("videoStage").addEventListener("wheel", (event) => {
  if (state.mode !== "MANUAL" || document.activeElement !== $("videoStage")) return;
  event.preventDefault();
  state.wheelUp = event.deltaY < 0 ? 0.6 : -0.6;
  clearTimeout(state.wheelTimer);
  state.wheelTimer = setTimeout(() => { state.wheelUp = 0; requestHold(); }, 220);
}, { passive: false });

async function manualAction(path, button, label) {
  setBusy(button, true, label);
  try { await api(path, { method: "POST", body: path.endsWith("takeoff") ? JSON.stringify({ altitude_m: 1.5 }) : "{}" }); }
  catch (error) { showError(error.message); }
  finally { setBusy(button, false); }
}

function updateTelemetry(data) {
  const online = Boolean(data.ok);
  $("airsimStatus").classList.toggle("online", online);
  $("airsimStatusText").textContent = online ? "AirSim 在线" : "AirSim 离线";
  const number = (value, digits, suffix = "") => value === null || value === undefined || Number.isNaN(Number(value)) ? "--" : `${Number(value).toFixed(digits)}${suffix}`;
  $("altitudeValue").textContent = number(data.altitude, 1, " m");
  $("speedValue").textContent = number(data.speed, 1, " m/s");
  $("headingValue").textContent = number(data.heading, 0, "°");
  $("verticalSpeedValue").textContent = number(data.verticalSpeed, 1, " m/s");
  $("latitudeValue").textContent = number(data.latitude, 6);
  $("longitudeValue").textContent = number(data.longitude, 6);
  $("linkValue").textContent = online ? "正常" : "中断";
  $("rollValue").textContent = number(data.roll, 0, "°");
  $("pitchValue").textContent = number(data.pitch, 0, "°");
  $("yawValue").textContent = number(data.yaw, 0, "°");
  $("attitudeBall").style.transform = `rotate(${-Number(data.roll || 0)}deg) translateY(${Math.max(-35, Math.min(35, Number(data.pitch || 0))) * .25}px)`;
  $("headingTrack").style.transform = `translateX(${-Number(data.heading || 0) * .42}px)`;
}

async function pollTelemetry() {
  try { updateTelemetry(await api("/api/airsim/telemetry")); } catch { updateTelemetry({ ok: false }); }
}

document.querySelectorAll(".view-button").forEach((button) => button.onclick = () => {
  document.querySelectorAll(".view-button").forEach((item) => {
    const active = item === button;
    item.classList.toggle("active", active);
    item.setAttribute("aria-pressed", String(active));
  });
  const view = button.dataset.view;
  $("cameraTag").textContent = view === "down" ? "CAM 02 · DOWN" : "CAM 01 · FRONT";
  $("airsimVideo").src = `/api/airsim/stream?view=${view}&fps=15&t=${Date.now()}`;
});

$("planBtn").onclick = createMission;
$("missionInput").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); createMission(); } });
$("refreshMissionsBtn").onclick = loadMissions;
$("manualModeBtn").onclick = () => enterManual();
$("aiModeBtn").onclick = leaveManual;
$("takeoverBtn").onclick = () => enterManual($("takeoverBtn"));
$("holdBtn").onclick = requestHold;
$("takeoffBtn").onclick = () => manualAction("/api/control/takeoff", $("takeoffBtn"), "起飞中…");
$("landBtn").onclick = () => manualAction("/api/control/land", $("landBtn"), "降落中…");
$("urlSelect").onchange = () => {};
document.querySelectorAll(".prompt-chip").forEach((button) => {
  button.onclick = () => {
    $("missionInput").value = button.dataset.prompt || "";
    $("missionInput").focus();
  };
});

setInterval(() => { $("clock").textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false }); }, 1000);
setInterval(pollTelemetry, 1000);
setInterval(refreshControl, 1500);
pollTelemetry();
refreshControl();
loadMissions();
connectEventSocket();
