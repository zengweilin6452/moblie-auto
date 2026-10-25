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
  pickedLocator: null, // {by, value, expression, risk}
  hoverNodeIndex: null,
  lastFrameAt: 0,
};

const qs = () => (state.device ? `?device=${encodeURIComponent(state.device)}` : "");

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
  }
  select.value = devices.some((d) => d.serial === current) || !current ? current : "";
}

async function switchDevice(serial) {
  state.device = serial;
  closePreviewSocket();
  setView("live"); // 含 clearInspectInfo:清空旧设备的检查产物
  state.deviceSpace = null;
  connectPreview(); // 旧连接已关且自动重连回调被清除:按新设备寻址重建
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
  const img = $("preview");
  if (!img.naturalWidth || !bounds) return;
  const spaceW = state.deviceSpace ? state.deviceSpace.w : img.naturalWidth;
  const spaceH = state.deviceSpace ? state.deviceSpace.h : img.naturalHeight;
  const scaleX = MobileCoords.displayScale(img.clientWidth, spaceW);
  const scaleY = MobileCoords.displayScale(img.clientHeight, spaceH);
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

/* 浏览器缩放/留白:CSS 坐标 → 设备像素(快照或流帧的设备空间) */
function toPixelCoords(event) {
  const img = $("preview");
  const rect = img.getBoundingClientRect();
  return MobileCoords.eventToPixel(
    event,
    rect,
    state.deviceSpace ? state.deviceSpace.w : img.naturalWidth,
    state.deviceSpace ? state.deviceSpace.h : img.naturalHeight,
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

/* ── 直接控制(仅实时视图)────────────────────────────── */

const control = { enabled: false, dragStart: null };

async function apiPost(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(`${data.code || res.status}:${data.message || ""}`);
  return data;
}

async function setControlMode(enabled) {
  if (enabled && !confirm("直接控制会向真机发送点击/滑动。\n确认启用?")) return;
  try {
    await apiPost("/api/v1/control", { enabled });
  } catch (err) {
    $("control-note").textContent = `切换失败 ${err.message}`;
    enabled = false;
  }
  control.enabled = enabled;
  $("control-toggle").checked = enabled;
  $("stage").classList.toggle("control-mode", enabled);
  for (const id of ["btn-key-back", "btn-key-home", "btn-wake"]) {
    $(id).disabled = !enabled;
  }
  if (enabled) setView("live"); // 注入按实时画面坐标计算,控制只能在实时视图
  $("control-note").textContent = enabled
    ? "控制中:点击画面=单击,拖动=滑动"
    : "";
}

async function inject(path, body) {
  if (!control.enabled) return;
  try {
    await apiPost(path + qs(), body || {});
  } catch (err) {
    $("control-note").textContent = `注入失败 ${err.message}`;
  }
}

function normCoords(event) {
  const rect = $("preview").getBoundingClientRect();
  return {
    nx: Math.min(Math.max((event.clientX - rect.left) / rect.width, 0), 1),
    ny: Math.min(Math.max((event.clientY - rect.top) / rect.height, 0), 1),
  };
}

function setupControl() {
  $("control-toggle").addEventListener("change", (event) => {
    setControlMode(event.target.checked);
  });
  $("btn-key-back").addEventListener("click", () => inject("/api/v1/input/key", { name: "back" }));
  $("btn-key-home").addEventListener("click", () => inject("/api/v1/input/key", { name: "home" }));
  $("btn-wake").addEventListener("click", () => inject("/api/v1/input/wake"));

  const img = $("preview");
  img.addEventListener("mousedown", (event) => {
    if (!control.enabled || state.view !== "live") return;
    control.dragStart = { ...normCoords(event), at: performance.now(), clientX: event.clientX, clientY: event.clientY };
    event.preventDefault();
  });
  window.addEventListener("mouseup", (event) => {
    if (!control.enabled || !control.dragStart || state.view !== "live") return;
    const start = control.dragStart;
    control.dragStart = null;
    const end = normCoords(event);
    const movedPx = Math.hypot(event.clientX - start.clientX, event.clientY - start.clientY);
    if (movedPx < 12) {
      inject("/api/v1/input/touch", { nx: end.nx, ny: end.ny });
    } else {
      inject("/api/v1/input/swipe", {
        nx0: start.nx, ny0: start.ny, nx1: end.nx, ny1: end.ny,
        duration_ms: Math.max(performance.now() - start.at, 120),
      });
    }
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
            ? ""
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

/* 滞后角标:实时视图下 >3s 无新帧提示(不遮画面) */
setInterval(() => {
  const stale = state.view === "live" && performance.now() - state.lastFrameAt > 3000;
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
setupTabs();
window.addEventListener("pagehide", () => freezeControl.release()); // 卸载前释放冻结引用
refreshStatus();
setInterval(refreshStatus, 3000);
connectPreview();
