/* mobile-auto 查看器:多设备实时预览、冻结检查(控件/图像)与定位验证。 */
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  device: "", // "" = 服务端配置的默认设备
  view: "live", // "live" 实时流 | "inspect" 冻结检查
  snapshotId: null,
  consistent: false,
  treeNodes: [],
  treeCapturedAt: null,
  deviceSpace: null, // {w, h}:当前画面(流帧或快照)的设备像素空间
  h264Active: false, // H.264 直解档(canvas)是否在用
  videoNote: "", // 直解档降级说明(JPEG 帧刷新时保留,不被覆盖)
  pickedLocator: null, // {by, value, expression, risk}
  hoverNodeIndex: null,
  lastFrameAt: 0,
};

const qs = () => (state.device ? `?device=${encodeURIComponent(state.device)}` : "");

/* 当前画面媒体元素:H.264 直解档为 canvas,JPEG 档/冻结快照为 img */
function mediaEl() {
  return state.h264Active ? $("video-canvas") : $("preview");
}
function mediaWidth(el) {
  return el.naturalWidth || el.width;
}
function mediaHeight(el) {
  return el.naturalHeight || el.height;
}
function showCanvas(on) {
  state.h264Active = on;
  $("stage").classList.toggle("h264", on);
}

/* ── 状态与设备 ─────────────────────────────────────────── */

async function refreshStatus() {
  const res = await fetch(`/api/v1/status${qs()}`);
  const status = await res.json();
  $("service").textContent = `mobile-auto viewer v${status.service_version}`;
  const d = status.device ?? {};
  $("device-state").textContent = `设备 ${d.serial ?? "未连接"} · ${d.state ?? "未知"}`;
  $("lease-state").textContent = d.busy ? "占用中(测试运行,只读观察不受影响)" : "空闲";
  $("btn-inspect").disabled = !status.capture.can_capture;
  populateDevices(status.devices ?? []);
}

function populateDevices(devices) {
  const select = $("device-select");
  const current = state.device;
  select.textContent = "";
  const defaultDev = devices.find((d) => d.default);
  const option = document.createElement("option");
  option.value = "";
  option.textContent = defaultDev ? `默认设备 ${defaultDev.serial}` : "默认设备";
  select.appendChild(option);
  for (const d of devices) {
    if (d.default) continue;
    const opt = document.createElement("option");
    opt.value = d.serial;
    const mark = { device: "", connected: "·已连", unauthorized: "·未授权", offline: "·离线" }[d.state] ?? "";
    opt.textContent = `${d.serial}${mark}`;
    select.appendChild(opt);
    if (d.state === "device") {
      // 已授权在线设备:拉取档案,型号补进选项文本(进程内缓存一次)
      fetchDeviceInfo(d.serial).then((info) => {
        if (info && info.model && opt.isConnected) {
          opt.textContent = `${d.serial}·${info.model}${mark}`;
        }
      });
    }
  }
  select.value = devices.some((d) => d.serial === current) || !current ? current : "";
  renderDeviceInfo(defaultDev ? defaultDev.serial : current);
}

/* ── 设备档案(P2 工单 06):getprop 只读探测,前端缓存不轮询 ── */

const deviceInfoCache = new Map();

async function fetchDeviceInfo(serial) {
  if (!serial) return null;
  if (deviceInfoCache.has(serial)) return deviceInfoCache.get(serial) ?? null;
  try {
    const res = await fetch(`/api/v1/devices/${encodeURIComponent(serial)}/info`);
    if (!res.ok) throw new Error(String(res.status));
    deviceInfoCache.set(serial, await res.json());
  } catch {
    deviceInfoCache.set(serial, null);
  }
  renderDeviceInfo(state.device || serial);
  return deviceInfoCache.get(serial) ?? null;
}

function renderDeviceInfo(serial) {
  const el = $("device-info");
  if (!el) return;
  const info = serial ? deviceInfoCache.get(serial) : null;
  if (!info || (!info.brand && !info.model && !info.android_version && !info.resolution)) {
    el.textContent = "";
    return;
  }
  const parts = [
    [info.brand, info.model].filter(Boolean).join(" "),
    info.android_version && `Android ${info.android_version}`,
    info.resolution,
  ];
  el.textContent = parts.filter(Boolean).join(" · ");
}

async function switchDevice(serial) {
  state.device = serial;
  closeVideo();
  closePreviewSocket();
  setView("live"); // 含 clearInspectInfo:清空旧设备的检查产物
  state.deviceSpace = null;
  connectVideo(); // 按新设备重新探测能力并接入实时画面
  refreshStatus();
}

/* ── 视图状态:实时 / 检查 ─────────────────────────────── */

function setView(view) {
  state.view = view;
  const badge = $("mode-badge");
  badge.textContent = view === "live" ? "实时" : "检查(冻结)";
  badge.classList.toggle("inspect", view === "inspect");
  $("btn-live").classList.toggle("hidden", view !== "inspect");
  $("stale-badge").classList.add("hidden");
  if (view === "live") {
    clearOverlay();
    state.snapshotId = null;
    clearInspectInfo();
    freezeControl.release(); // 冻结引用不释放会把实时快照配额挤满
    setFreezeNotice("");
    reconnectMedia(); // 检查期间 canvas 让位给快照 img,回实时按需接回
  } else {
    showCanvas(false); // 冻结快照显示在 img 上,canvas 必须让位
  }
}

/* 回到实时(或切换设备)时,清空附着在冻结快照上的检查产物:
   控件页的属性表/候选/表达式,图像页的框选区域,以及树面板。
   已注册的模板(logical/sha256/表达式)不属于快照,保留不清。 */
function clearInspectInfo() {
  state.treeNodes = [];
  state.treeCapturedAt = null;
  state.pickedLocator = null;
  state.hoverNodeIndex = null;
  $("tree").textContent = "";
  $("tree-time").textContent = "";
  $("tree-meta").textContent = "「采集检查」后显示;点击树节点联动选中。";
  fillAttrTable(null);
  renderCandidates([]);
  $("pick-point").textContent = "";
  imageState.region = null;
  renderRegionTable(null);
  $("image-hint").textContent = "先「采集检查」冻结画面,再在画面上按住拖动框选目标。";
  $("btn-tpl-save").disabled = true;
}

/* 检查编辑期把快照固定为冻结引用:未冻结的实时快照 120 秒后被驱逐,
   编辑中途拾取/保存会 404。冻结快照不参与容量驱逐,只增不放会把实时
   配额挤满、新快照一创建就被驱逐,因此至多保持一份冻结引用:新的冻结
   前先释放旧的(状态机见 snapshot_freeze.js),回实时/切设备时释放。
   冻结失败不中断编辑,但底图会按实时有效期过期,必须提示。 */
const freezeControl = SnapshotFreeze.createSnapshotFreeze((id, frozen) =>
  fetch(`/api/v1/snapshots/${id}/freeze`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ frozen }),
    keepalive: true, // 页面卸载时的释放也能发出
  }).then((res) => {
    if (!res.ok) throw new Error(`freeze ${res.status}`);
  }),
);

function setFreezeNotice(text) {
  const el = $("freeze-notice");
  el.textContent = text;
  el.classList.toggle("hidden", !text);
}

async function freezeSnapshot(id) {
  try {
    await freezeControl.freeze(id);
    setFreezeNotice("");
  } catch {
    setFreezeNotice(
      "冻结失败:编辑底图未被固定,约 120 秒后过期;若拾取/保存报快照过期,请重新「采集检查」。",
    );
  }
}

async function inspectCapture() {
  clearInspectInfo(); // 新快照:清掉上一次冻结检查的属性/候选/框选
  const res = await fetch(`/api/v1/snapshots${qs()}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ with_tree: true }),
  });
  const data = await res.json();
  if (!res.ok) {
    $("tree-meta").textContent = `采集失败(${data.code}):${data.message}`;
    return;
  }
  state.snapshotId = data.snapshot_id;
  state.consistent = data.consistent === true && !data.tree_error;
  state.treeCapturedAt = data.tree_captured_at;
  state.deviceSpace = { w: data.width, h: data.height };
  freezeSnapshot(data.snapshot_id); // 编辑期固定引用:不冻结 120 秒后被驱逐
  setView("inspect");
  $("preview").src = `/api/v1/snapshots/${data.snapshot_id}/image`;
  $("preview-time").textContent = `冻结于 ${data.captured_at}`;
  $("tree-time").textContent = data.tree_captured_at ?? "";
  $("tree-meta").textContent = data.tree_error
    ? `UI 树不可用:${data.tree_error}(原生拾取禁用,图像定位不受影响)`
    : data.consistent
      ? `${data.width}x${data.height} · ${data.orientation} · 可拾取`
      : `${data.width}x${data.height} · 截图与树间隔超限,画面可能不一致,拾取已禁用;请重新采集`;
  if (!data.tree_error) {
    const treeRes = await fetch(`/api/v1/snapshots/${data.snapshot_id}/tree`);
    if (treeRes.ok) {
      state.treeNodes = (await treeRes.json()).nodes;
      renderTree($("tree-filter").value);
    }
  } else {
    state.treeNodes = [];
    $("tree").textContent = "";
  }
  clearOverlay();
  renderCandidates([]);
}

/* ── UI 树与双向拾取 ─────────────────────────────────────── */

function nodeLabel(node) {
  return (
    [node.class_name.split(".").pop(), node.resource_id, node.text, node.content_desc]
      .filter(Boolean)
      .join(" · ") + (node.bounds ? `  [${node.bounds.join(",")}]` : "")
  );
}

function renderTree(filter) {
  const ul = $("tree");
  ul.textContent = "";
  const needle = (filter || "").trim().toLowerCase();
  for (const node of state.treeNodes) {
    if (!node.text && !node.resource_id && !node.content_desc) continue;
    if (needle && !nodeLabel(node).toLowerCase().includes(needle)) continue;
    const li = document.createElement("li");
    li.className = node.clickable ? "clickable" : "";
    li.dataset.index = node.index;
    li.title = nodeLabel(node);
    li.textContent = nodeLabel(node);
    li.addEventListener("click", () => onTreeNodeClick(node));
    ul.appendChild(li);
  }
}

function highlightNode(node) {
  for (const li of $("tree").children) {
    li.classList.toggle("selected", node && li.dataset.index == node.index);
  }
}

async function onTreeNodeClick(node) {
  highlightNode(node);
  if (node.bounds) drawBox(node.bounds, "tree-box");
  fillAttrTable(node);
  if (!node.bounds || !state.consistent) return;
  // 命中点取节点中心:复用服务端候选生成(与驱动同一套定位规则)
  const cx = Math.floor((node.bounds[0] + node.bounds[2]) / 2);
  const cy = Math.floor((node.bounds[1] + node.bounds[3]) / 2);
  await pickAtPoint(cx, cy);
}

function drawBox(bounds, cls) {
  clearOverlay(cls === "hover-box" ? null : cls);
  const el = mediaEl();
  if (!bounds || !el.clientWidth) return;
  const spaceW = state.deviceSpace ? state.deviceSpace.w : mediaWidth(el);
  const spaceH = state.deviceSpace ? state.deviceSpace.h : mediaHeight(el);
  const scaleX = MobileCoords.displayScale(el.clientWidth, spaceW);
  const scaleY = MobileCoords.displayScale(el.clientHeight, spaceH);
  const box = document.createElement("div");
  box.className = cls || "highlight";
  box.style.left = `${bounds[0] * scaleX}px`;
  box.style.top = `${bounds[1] * scaleY}px`;
  box.style.width = `${(bounds[2] - bounds[0]) * scaleX}px`;
  box.style.height = `${(bounds[3] - bounds[1]) * scaleY}px`;
  $("overlay").appendChild(box);
}

function clearOverlay(cls) {
  for (const box of [...$("overlay").children]) {
    if (!cls || box.className === cls) box.remove();
  }
}

/* 指针位置 → 归一化坐标(以实际画面元素显示盒为准,容器与画面完全重合) */
function normCoords(event) {
  const rect = mediaEl().getBoundingClientRect();
  const clamp = (v) => Math.min(Math.max(v, 0), 1);
  return {
    nx: clamp(rect.width ? (event.clientX - rect.left) / rect.width : 0),
    ny: clamp(rect.height ? (event.clientY - rect.top) / rect.height : 0),
  };
}

/* 浏览器缩放/留白:CSS 坐标 → 设备像素(快照或流帧的设备空间) */
function toPixelCoords(event) {
  const el = mediaEl();
  const rect = el.getBoundingClientRect();
  return MobileCoords.eventToPixel(
    event,
    rect,
    state.deviceSpace ? state.deviceSpace.w : mediaWidth(el),
    state.deviceSpace ? state.deviceSpace.h : mediaHeight(el),
  );
}

async function pickAt(event) {
  if (control.enabled || state.view !== "inspect") return;
  if (!imageGuard()) return;
  const { x, y } = toPixelCoords(event);
  await pickAtPoint(x, y, event);
}

function imageGuard() {
  if (imageTabActive()) return false; // 图像模式:画面拖动是框选
  if (!state.snapshotId) {
    $("tree-meta").textContent = "请先「采集检查」冻结画面。";
    return false;
  }
  if (!state.consistent) {
    $("tree-meta").textContent = "快照不一致或无 UI 树,拾取已禁用;请重新采集。";
    return false;
  }
  return true;
}

async function pickAtPoint(x, y, event) {
  if (!state.snapshotId || !state.consistent) return;
  const res = await fetch(`/api/v1/pick`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ snapshot_id: state.snapshotId, x, y }),
  });
  const data = await res.json();
  if (!res.ok) {
    $("pick-point").textContent =
      res.status === 404 ? "快照已过期,请重新采集" : `拾取失败(${data.code})`;
    return;
  }
  const img = $("preview");
  const pctX = event ? ((event.clientX - img.getBoundingClientRect().left) / img.clientWidth * 100).toFixed(1) : null;
  const pctY = event ? ((event.clientY - img.getBoundingClientRect().top) / img.clientHeight * 100).toFixed(1) : null;
  const pctText = pctX !== null ? ` · 点击 (${pctX}%, ${pctY}%)` : "";
  $("pick-point").textContent = `@ (${x}, ${y})${pctText}${data.overlap ? " · 存在重叠,请选择" : ""}`;
  renderCandidates(data.candidates);
  if (data.candidates.length) fillAttrTable(data.candidates[0]);
}

/* ── 元素信息面板(检查栏)──────────────────────────────── */

function fillAttrTable(node) {
  const tbody = $("attr-table").querySelector("tbody");
  tbody.textContent = "";
  if (!node) return;
  const bounds = node.bounds;
  const rows = [
    ["class", node.class_name],
    ["resource-id", node.resource_id],
    ["text", node.text],
    ["content-desc", node.content_desc],
    ["clickable", node.clickable === true ? "true" : node.clickable === false ? "false" : ""],
    ["bounds", bounds ? bounds.join(", ") : ""],
  ];
  for (const [key, value] of rows) {
    if (value === undefined || value === null || value === "") continue;
    const tr = document.createElement("tr");
    const tdKey = document.createElement("td");
    tdKey.textContent = key;
    const tdVal = document.createElement("td");
    tdVal.textContent = String(value);
    if (String(value).length > 6) {
      const btn = document.createElement("button");
      btn.className = "attr-copy";
      btn.type = "button";
      btn.textContent = "复制";
      btn.addEventListener("click", async () => {
        await navigator.clipboard.writeText(String(value));
        btn.textContent = "已复制";
        setTimeout(() => (btn.textContent = "复制"), 1200);
      });
      tdVal.appendChild(btn);
    }
    tr.append(tdKey, tdVal);
    tbody.appendChild(tr);
  }
}

function renderCandidates(nodeCandidates) {
  const ul = $("candidate-list");
  ul.textContent = "";
  state.pickedLocator = null;
  $("btn-validate").disabled = true;
  $("btn-copy").disabled = true;
  $("expression").textContent = "";
  $("validate-note").textContent = "";
  if (!nodeCandidates.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "点击画面或树节点后列出候选。";
    ul.appendChild(li);
    return;
  }
  for (const node of nodeCandidates) {
    for (const loc of node.locators) {
      const li = document.createElement("li");
      li.className = "candidate";
      li.textContent = `[${loc.by}] ${loc.value}  ${loc.risk !== "stable" ? "⚠ " + loc.risk : ""}`;
      li.addEventListener("click", () => selectCandidate(li, loc, node));
      ul.appendChild(li);
    }
  }
}

function selectCandidate(li, loc, node) {
  for (const item of $("candidate-list").children) item.classList.remove("selected");
  li.classList.add("selected");
  state.pickedLocator = loc;
  $("expression").textContent = loc.expression;
  $("btn-validate").disabled = false;
  $("btn-copy").disabled = false;
  if (node.bounds) drawBox(node.bounds, "pick-box");
  const hit = state.treeNodes.find(
    (n) => n.bounds && node.bounds && n.bounds.join(",") === node.bounds.join(","),
  );
  if (hit) highlightNode(hit);
}

/* ── 悬停命中(ui-viewer 式):最小包含节点虚线框 ────────── */

function hitTest(x, y) {
  let best = null;
  let bestArea = Infinity;
  for (const node of state.treeNodes) {
    const b = node.bounds;
    if (!b) continue;
    if (b[0] <= x && x < b[2] && b[1] <= y && y < b[3]) {
      const area = (b[2] - b[0]) * (b[3] - b[1]);
      if (area < bestArea) {
        best = node;
        bestArea = area;
      }
    }
  }
  return best;
}

function setupHover() {
  const img = $("preview");
  img.addEventListener("mousemove", (event) => {
    if (control.enabled || state.view !== "inspect" || !state.consistent || imageTabActive()) return;
    const { x, y } = toPixelCoords(event);
    const node = hitTest(x, y);
    if ((node?.index ?? null) === state.hoverNodeIndex) return;
    state.hoverNodeIndex = node?.index ?? null;
    for (const box of [...$("overlay").children]) {
      if (box.className === "hover-box") box.remove();
    }
    if (node) drawBox(node.bounds, "hover-box");
  });
  img.addEventListener("mouseleave", () => {
    state.hoverNodeIndex = null;
    clearOverlay("hover-box");
  });
}

/* ── 定位验证 ────────────────────────────────────────────── */

async function validatePicked() {
  if (!state.pickedLocator) return;
  $("validate-note").textContent = "验证中(重新采集查询)…";
  const res = await fetch(`/api/v1/locators/validate${qs()}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ target: state.pickedLocator.target }),
  });
  const data = await res.json();
  if (!res.ok) {
    $("validate-note").textContent = `验证失败(${data.code}):${data.message}`;
    return;
  }
  state.snapshotId = data.snapshot_id;
  freezeSnapshot(data.snapshot_id); // 验证结果成为新的编辑底图,同样固定
  $("preview").src = `/api/v1/snapshots/${data.snapshot_id}/image`;
  for (const m of data.matches) drawBox([m.x, m.y, m.x + m.width, m.y + m.height], "match-box");
  $("validate-note").textContent = `匹配 ${data.count} 个 · ${data.note}`;
}

/* ── 单操作者控制(凭据制;仅实时视图)────────────────── */

const control = {
  enabled: false,
  token: null, // 操作者凭据:仅页面内存,不落 URL/存储
  heartbeat: null,
  seq: 0, // 手势消息序号(单调递增)
  dragStart: null,
  gesture: null,
};

async function apiPost(path, body, token) {
  const headers = { "content-type": "application/json" };
  if (token) headers["x-viewer-control"] = token;
  const res = await fetch(path, {
    method: "POST",
    headers,
    body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(`${data.code || res.status}:${data.message || ""}`);
  return data;
}

async function setControlMode(enabled) {
  if (enabled) {
    if (
      !confirm(
        "直接控制会向真机发送点击/滑动。\n确认启用?(单操作者:他人持有控制权时会被拒绝)",
      )
    ) {
      $("control-toggle").checked = false;
      return;
    }
    try {
      const data = await apiPost("/api/v1/control", { enabled: true }, control.token);
      control.token = data.token;
      startControlHeartbeat();
    } catch (err) {
      $("control-note").textContent = `切换失败 ${err.message}`;
      $("control-toggle").checked = false;
      return;
    }
  }
  control.enabled = enabled;
  $("control-toggle").checked = enabled;
  $("stage").classList.toggle("control-mode", enabled);
  for (const id of ["btn-key-back", "btn-key-home", "btn-wake"]) {
    $(id).disabled = !enabled;
  }
  if (enabled) {
    setView("live"); // 注入按实时画面坐标计算,控制只能在实时视图
    $("control-note").textContent =
      "控制中:按住拖动=滑动,单击=点击(约 1 秒心跳续期)";
  } else {
    stopControlHeartbeat();
    const token = control.token;
    control.token = null;
    if (token) apiPost("/api/v1/control", { enabled: false }, token).catch(() => {});
    $("control-note").textContent = "";
  }
}

function startControlHeartbeat() {
  stopControlHeartbeat();
  control.heartbeat = setInterval(async () => {
    try {
      await apiPost("/api/v1/control/heartbeat", {}, control.token);
    } catch {
      // 心跳被拒(失联被撤权/他人领用):本地降级,等待操作者重新启用
      stopControlHeartbeat();
      control.enabled = false;
      control.token = null;
      control.gesture = null;
      $("control-toggle").checked = false;
      $("stage").classList.remove("control-mode");
      for (const id of ["btn-key-back", "btn-key-home", "btn-wake"]) {
        $(id).disabled = true;
      }
      $("control-note").textContent = "控制权已失效,请重新启用";
    }
  }, 1000);
}

function stopControlHeartbeat() {
  if (control.heartbeat) {
    clearInterval(control.heartbeat);
    control.heartbeat = null;
  }
}

async function inject(path, body) {
  if (!control.enabled) return;
  try {
    await apiPost(path + qs(), body || {}, control.token);
  } catch (err) {
    $("control-note").textContent = `注入失败 ${err.message}`;
  }
}

/* 连续手势:拖动全程走 gesture WS(DOWN/MOVE/UP 逐条发送),
   单击=同点 start+end;MOVE 按 20Hz 客户端节流;连接在 end 后关闭。 */
function startGesture(nx, ny) {
  if (!control.token) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/api/v1/input/gesture${qs()}`);
  const g = { ws, queue: [], lastSentAt: 0, done: false };
  control.gesture = g;
  g.queue.push({ token: control.token });
  g.queue.push({ type: "start", nx, ny, seq: ++control.seq });
  ws.onopen = () => {
    for (const msg of g.queue) ws.send(JSON.stringify(msg));
    g.queue = [];
  };
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.kind === "error") {
      $("control-note").textContent = `手势失败(${msg.code}):${msg.message}`;
      g.done = true;
      if (control.gesture === g) control.gesture = null;
      ws.close();
    } else if (msg.kind === "ended") {
      g.done = true;
      if (control.gesture === g) control.gesture = null;
      ws.close();
    }
  };
  ws.onclose = () => {
    if (control.gesture === g) control.gesture = null;
  };
}

function moveGesture(nx, ny) {
  const g = control.gesture;
  if (!g || g.done) return;
  const now = performance.now();
  if (now - g.lastSentAt < 50) return; // 20Hz 客户端节流
  g.lastSentAt = now;
  if (g.ws.readyState === WebSocket.OPEN) {
    g.ws.send(JSON.stringify({ type: "move", nx, ny, seq: ++control.seq }));
  }
}

function endGesture() {
  const g = control.gesture;
  if (!g || g.done) return;
  control.gesture = null;
  const msg = { type: "end", seq: ++control.seq };
  if (g.ws.readyState === WebSocket.OPEN) g.ws.send(JSON.stringify(msg));
  else g.queue.push(msg); // 建连未完成:排队,onopen 依序发出
}

function setupControl() {
  $("control-toggle").addEventListener("change", (event) => {
    setControlMode(event.target.checked);
  });
  $("btn-key-back").addEventListener("click", () => inject("/api/v1/input/key", { name: "back" }));
  $("btn-key-home").addEventListener("click", () => inject("/api/v1/input/key", { name: "home" }));
  $("btn-wake").addEventListener("click", () => inject("/api/v1/input/wake"));

  // 指针事件挂在 stage:两种画面档(img/canvas)统一处理
  const stage = $("stage");
  stage.addEventListener("pointerdown", (event) => {
    if (!control.enabled || state.view !== "live" || event.button !== 0) return;
    const { nx, ny } = normCoords(event);
    control.dragStart = {
      nx, ny, moved: false,
      clientX: event.clientX, clientY: event.clientY,
    };
    startGesture(nx, ny);
    event.preventDefault();
  });
  stage.addEventListener("pointermove", (event) => {
    if (!control.dragStart) return;
    const drag = control.dragStart;
    if (!drag.moved) {
      drag.moved =
        Math.hypot(event.clientX - drag.clientX, event.clientY - drag.clientY) >= 12;
    }
    if (drag.moved) {
      const { nx, ny } = normCoords(event);
      moveGesture(nx, ny);
    }
  });
  window.addEventListener("pointerup", (event) => {
    if (!control.dragStart) return;
    control.dragStart = null;
    if (state.view === "live") endGesture();
  });
}

/* ── 标签页 ──────────────────────────────────────────────── */

function imageTabActive() {
  return document.querySelector(".tab.active").dataset.tab === "image";
}

function setupTabs() {
  for (const btn of document.querySelectorAll(".tab")) {
    btn.addEventListener("click", () => {
      for (const b of document.querySelectorAll(".tab")) b.classList.remove("active");
      btn.classList.add("active");
      $("tab-native").classList.toggle("hidden", btn.dataset.tab !== "native");
      $("tab-image").classList.toggle("hidden", btn.dataset.tab !== "image");
    });
  }
}

/* ── 预览 WS(实时流;设备寻址;状态用提示行呈现,不遮画面)── */

let previewSocket = null;

function closePreviewSocket() {
  if (previewSocket) {
    const ws = previewSocket;
    previewSocket = null;
    ws.onclose = null;
    ws.close();
  }
}

function connectPreview() {
  if (previewSocket) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/api/v1/preview${qs()}`);
  previewSocket = ws;
  ws.onmessage = (event) => {
    const frame = JSON.parse(event.data);
    $("freshness").textContent = `帧时间 ${frame.captured_at}`;
    if (frame.kind === "frame") {
      state.lastFrameAt = performance.now();
      if (frame.screen_width) {
        // 检查冻结期间不覆盖快照的设备空间
        if (state.view === "live" || !state.deviceSpace) {
          state.deviceSpace = { w: frame.screen_width, h: frame.screen_height };
        }
      }
      if (state.view === "live") {
        $("preview").src = `data:image/jpeg;base64,${frame.jpeg_base64}`;
        $("preview-time").textContent =
          frame.source === "scrcpy"
            ? `${frame.captured_at} · ${frame.width}x${frame.height}`
            : frame.captured_at;
        setStreamNotice(
          frame.source === "scrcpy"
            ? state.videoNote || ""
            : `视频流不可用,截图轮询中${frame.fallback_reason ? ":" + frame.fallback_reason : ""}`,
        );
      }
    } else if (frame.kind === "status") {
      setStreamNotice(frame.message || "预览暂不可用");
    } else {
      setStreamNotice(frame.message || "预览暂不可用");
    }
  };
  ws.onclose = () => {
    if (previewSocket === ws) previewSocket = null;
    setTimeout(connectPreview, 2000);
  };
}

function setStreamNotice(text) {
  const el = $("stream-notice");
  el.textContent = text;
  el.classList.toggle("hidden", !text);
}

/* ── H.264 直解档(web-preview 03):优先,失败回退 JPEG ── */

let h264Player = null;
let fittedVideo = { w: 0, h: 0 };
const FIRST_FRAME_BUDGET_MS = 2000; // 直解首次出帧预算,超时继续 JPEG(规格 §用户旅程)

async function connectVideo() {
  let caps = null;
  try {
    const res = await fetch(`/api/v1/preview/capabilities${qs()}`);
    caps = await res.json();
  } catch {
    caps = null;
  }
  if (!caps || !caps.h264_ws || typeof VideoDecoder === "undefined") {
    setStreamNotice(
      caps && caps.reason
        ? `H.264 直解不可用,使用 JPEG 预览档:${caps.reason}`
        : "浏览器不支持 WebCodecs,使用 JPEG 预览档",
    );
    connectPreview();
    return;
  }
  // 先出 JPEG:首个 H.264 帧实际绘制后才切 Canvas 并释放 JPEG 连接;
  // 预算从服务端 ready(通道已起)起算——通道冷启动不计入,超预算未出帧
  // (如已起流通道要等下一关键帧)就继续 JPEG 并提示。
  showCanvas(false);
  fittedVideo = { w: 0, h: 0 };
  state.videoNote = "";
  let switched = false;
  let budget = null;
  const startBudget = () => {
    if (switched || budget) return;
    budget = setTimeout(() => {
      if (switched) return;
      closeVideo();
      state.videoNote =
        "H.264 直解 2 秒内未出帧,继续 JPEG 预览档(切设备或重连时重试)";
      setStreamNotice(state.videoNote);
    }, FIRST_FRAME_BUDGET_MS);
  };
  h264Player = MobileVideo.createH264Player({
    canvas: $("video-canvas"),
    onFrame: () => {
      if (!switched) {
        switched = true;
        clearTimeout(budget);
        closePreviewSocket();
        showCanvas(true);
        state.videoNote = "";
      }
      state.lastFrameAt = performance.now();
      $("freshness").textContent = "H.264 直解";
      setStreamNotice("");
      const canvas = $("video-canvas");
      if (canvas.width !== fittedVideo.w || canvas.height !== fittedVideo.h) {
        fittedVideo = { w: canvas.width, h: canvas.height };
        fitStage(); // 首帧与分辨率变化时按 canvas 尺寸重排
      }
    },
    onSession: ({ w, h }) => {
      // 检查冻结期间不覆盖快照的设备空间
      if (state.view === "live" || !state.deviceSpace) {
        state.deviceSpace = { w, h };
      }
    },
    onState: (kind, message) => {
      if (kind === "ready") {
        startBudget();
      } else if (kind === "error" || kind === "closed") {
        clearTimeout(budget);
        showCanvas(false);
        h264Player = null;
        state.videoNote = `H.264 直解中断,回退 JPEG 档${message ? ":" + message : ""}`;
        setStreamNotice(state.videoNote);
        connectPreview();
      }
    },
  });
  h264Player.open(qs());
  // JPEG 档稍后接入:让直解 WS 先到服务端,通道冷启动时订阅能挂上首个
  // 关键帧(出生订阅);直解失败/超预算时 JPEG 照常显示。
  setTimeout(() => {
    if (!switched) connectPreview();
  }, 300);
}

function closeVideo() {
  if (h264Player) {
    h264Player.close();
    h264Player = null;
  }
  showCanvas(false);
}

/* 按当前能力接入实时画面:直解已出帧就回 canvas,否则 JPEG 预览档 */
function reconnectMedia() {
  if (state.h264Active && h264Player && h264Player.active) {
    closePreviewSocket();
    showCanvas(true);
    fitStage();
  } else {
    showCanvas(false);
    connectPreview();
  }
}

/* 滞后角标:JPEG 档按 10Hz 轮询,>3s 无新帧才算滞后;H.264 编码器只在
   画面变化时出帧,静屏长时间无帧属正常,由 WS 错误/关闭触发回退提示。 */
setInterval(() => {
  const stale =
    state.view === "live" &&
    !state.h264Active &&
    performance.now() - state.lastFrameAt > 3000;
  $("stale-badge").classList.toggle("hidden", !stale);
}, 1000);

/* ── 图像模板制作(检查模式框选)────────────────────────── */

const imageState = { region: null, logical: null, sha256: null };

function renderRegionTable(region) {
  const tbody = $("region-table").querySelector("tbody");
  tbody.textContent = "";
  if (!region) return;
  const [x, y, w, h] = region;
  const space = state.deviceSpace ?? { w: 0, h: 0 };
  const rows = [
    ["region (x,y,w,h)", region.join(", ")],
    ["百分比", `${((w / 2 + x) / (space.w || 1) * 100).toFixed(1)}%, ${((h / 2 + y) / (space.h || 1) * 100).toFixed(1)}%(中心)`],
  ];
  for (const [key, value] of rows) {
    const tr = document.createElement("tr");
    const tdKey = document.createElement("td");
    tdKey.textContent = key;
    const tdVal = document.createElement("td");
    tdVal.textContent = String(value);
    tr.append(tdKey, tdVal);
    tbody.appendChild(tr);
  }
}

function setupMarquee() {
  const img = $("preview");
  let start = null;
  img.addEventListener("mousedown", (event) => {
    if (control.enabled) return;
    if (!imageTabActive() || state.view !== "inspect" || !state.snapshotId) return;
    start = toPixelCoords(event);
    const box = document.createElement("div");
    box.id = "marquee";
    $("overlay").appendChild(box);
    event.preventDefault();
  });
  window.addEventListener("mousemove", (event) => {
    if (!start) return;
    const now = toPixelCoords(event);
    const box = document.getElementById("marquee");
    if (!box) return;
    const spaceW = state.deviceSpace ? state.deviceSpace.w : img.naturalWidth;
    const spaceH = state.deviceSpace ? state.deviceSpace.h : img.naturalHeight;
    const scaleX = MobileCoords.displayScale(img.clientWidth, spaceW);
    const scaleY = MobileCoords.displayScale(img.clientHeight, spaceH);
    const x0 = Math.min(start.x, now.x), y0 = Math.min(start.y, now.y);
    const x1 = Math.max(start.x, now.x), y1 = Math.max(start.y, now.y);
    box.style.left = `${x0 * scaleX}px`;
    box.style.top = `${y0 * scaleY}px`;
    box.style.width = `${(x1 - x0) * scaleX}px`;
    box.style.height = `${(y1 - y0) * scaleY}px`;
  });
  window.addEventListener("mouseup", (event) => {
    if (!start) return;
    const now = toPixelCoords(event);
    document.getElementById("marquee")?.remove();
    const region = [
      Math.min(start.x, now.x),
      Math.min(start.y, now.y),
      Math.abs(now.x - start.x),
      Math.abs(now.y - start.y),
    ];
    start = null;
    if (region[2] < 4 || region[3] < 4) return; // 忽略误点
    imageState.region = region;
    renderRegionTable(region);
    $("image-hint").textContent = "已框选;填写模板名后保存。";
    $("btn-tpl-save").disabled = false;
    drawBox(region, "match-box");
  });
}

async function saveTemplate() {
  const logical = $("tpl-name").value.trim();
  if (!logical) { $("tpl-result").textContent = "请填写模板名(如 demo/target.png)。"; return; }
  const body = {
    snapshot_id: state.snapshotId,
    logical,
    region: imageState.region,
    note: "viewer-made",
  };
  const res = await fetch(`/api/v1/templates${qs()}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (res.status === 409) {
    const listRes = await fetch(`/api/v1/templates${qs()}`);
    const known = (await listRes.json()).templates.find((t) => t.logical === logical);
    if (!known) { $("tpl-result").textContent = `保存失败(${data.code}):${data.message}`; return; }
    if (!confirm(`模板 ${logical} 已存在,基于旧版本确认更新?`)) {
      $("tpl-result").textContent = "已取消更新。";
      return;
    }
    const upRes = await fetch(`/api/v1/templates/${encodeURIComponent(logical)}${qs()}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...body, expected_old_sha256: known.image_sha256 }),
    });
    const upData = await upRes.json();
    if (!upRes.ok) { $("tpl-result").textContent = `更新失败(${upData.code}):${upData.message}`; return; }
    imageState.sha256 = upData.sha256;
    imageState.logical = logical;
    $("tpl-result").textContent = `已更新为新版本 ${upData.version_id.slice(0, 8)}。`;
    setImageExpression();
    return;
  }
  if (!res.ok) { $("tpl-result").textContent = `保存失败(${data.code}):${data.message}`; return; }
  imageState.logical = logical;
  imageState.sha256 = data.sha256;
  $("tpl-result").textContent = `模板已保存(${data.width}x${data.height},摘要 ${data.sha256.slice(0, 10)}…)。`;
  setImageExpression();
}

function setImageExpression() {
  const confidence = parseFloat($("tpl-confidence").value);
  let expr = `Target(Locator.IMAGE, ${JSON.stringify(imageState.logical)}`;
  if (Number.isFinite(confidence) && confidence !== 0.90) {
    expr += `, confidence=${confidence}`;
  }
  if (imageState.region) {
    expr += `, region=${JSON.stringify(imageState.region)}`;
  }
  expr += ")";
  $("image-expression").textContent = expr;
  $("btn-tpl-validate").disabled = false;
  $("btn-image-copy").disabled = false;
}

async function validateTemplate() {
  if (!imageState.logical) return;
  const confidence = parseFloat($("tpl-confidence").value);
  const target = { by: "image", value: imageState.logical };
  if (Number.isFinite(confidence)) target.confidence = confidence;
  if (imageState.region) target.region = imageState.region;
  $("tpl-result").textContent = "重新匹配中(新截图)…";
  const res = await fetch(`/api/v1/templates/validate${qs()}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ target }),
  });
  const data = await res.json();
  if (!res.ok) { $("tpl-result").textContent = `验证失败(${data.code}):${data.message}`; return; }
  state.snapshotId = data.snapshot_id;
  freezeSnapshot(data.snapshot_id); // 验证结果成为新的编辑底图,同样固定
  $("preview").src = `/api/v1/snapshots/${data.snapshot_id}/image`;
  for (const m of data.matches) {
    drawBox([m.x, m.y, m.x + m.width, m.y + m.height], "match-box");
  }
  const scoreText = data.scores ? ` 分数 ${data.scores.map((s) => s.toFixed(3)).join(", ")}` : "";
  $("tpl-result").textContent = `匹配 ${data.count} 个 · ${data.note}.${scoreText}`;
}

/* ── 装配 ────────────────────────────────────────────────── */

$("btn-inspect").addEventListener("click", inspectCapture);
$("btn-live").addEventListener("click", () => setView("live"));
$("device-select").addEventListener("change", (event) => switchDevice(event.target.value));
$("tree-filter").addEventListener("input", (event) => renderTree(event.target.value));
$("btn-tpl-save").addEventListener("click", saveTemplate);
$("btn-tpl-validate").addEventListener("click", validateTemplate);
$("btn-image-copy").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("image-expression").textContent);
  $("btn-image-copy").textContent = "已复制";
  setTimeout(() => ($("btn-image-copy").textContent = "复制 IMAGE 定位"), 1200);
});
$("tpl-confidence").addEventListener("change", () => {
  if (imageState.logical) setImageExpression();
});
setupMarquee();
setupControl();
setupHover();
$("btn-validate").addEventListener("click", validatePicked);
$("btn-copy").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("expression").textContent);
  $("btn-copy").textContent = "已复制";
  setTimeout(() => ($("btn-copy").textContent = "复制表达式"), 1200);
});
$("preview").addEventListener("click", pickAt);

/* 画面尺寸:容器严格等于图片内容显示尺寸(坐标正确性的前提)。
   策略 = fit 进「栏可用宽 × 视口可用高」的等比盒子:先按宽度算高度,
   高度超限则改按高度反算宽度。容器与 img 尺寸一致 ⇒ rect 即内容盒,
   归一化坐标(getBoundingClientRect)与 overlay 定位都无 letterbox 偏移。
   触发:每帧 load(分辨率/宽高比变化)+ 窗口 resize。 */
function fitStage() {
  const pane = $("preview-pane");
  if (!pane) return;
  const el = mediaEl();
  const naturalW = mediaWidth(el);
  const naturalH = mediaHeight(el);
  if (!naturalW || !naturalH) return;
  const availW = pane.clientWidth - 2; // 减去 stage 边框
  const availH = Math.max(window.innerHeight - 230, 240);
  const ratio = naturalW / naturalH;
  let w = availW;
  let h = w / ratio;
  if (h > availH) {
    h = availH;
    w = h * ratio;
  }
  const stage = $("stage");
  stage.style.width = `${Math.round(w)}px`;
  stage.style.height = `${Math.round(h)}px`;
  stage.style.maxHeight = "none";
  el.style.width = "100%";
  el.style.height = "100%";
}

$("preview").addEventListener("load", fitStage);
window.addEventListener("resize", fitStage);
setupTabs();
window.addEventListener("pagehide", () => {
  freezeControl.release(); // 卸载前释放冻结引用
  stopControlHeartbeat();
  const token = control.token;
  if (token) {
    // 尽力释放控制权;失联路径上服务端也会在 5 秒后懒撤权
    fetch(`/api/v1/control${qs()}`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-viewer-control": token },
      body: JSON.stringify({ enabled: false }),
      keepalive: true,
    }).catch(() => {});
  }
});
refreshStatus();
setInterval(refreshStatus, 3000);
connectVideo();
