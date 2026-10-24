/* mobile-auto 查看器:实时预览、双向拾取与定位验证;仅观察,不控制设备。 */
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  snapshotId: null,
  consistent: false,
  treeNodes: [],
  pickedLocator: null, // {by, value, expression, risk}
  selectedNode: null,
};

/* ── 状态与快照 ─────────────────────────────────────────── */

async function refreshStatus() {
  const res = await fetch("/api/v1/status");
  const status = await res.json();
  $("service").textContent = `mobile-auto viewer v${status.service_version}`;
  $("device-state").textContent = `设备 ${status.device.serial ?? "未连接"} · ${status.device.state}`;
  $("lease-state").textContent = status.device.busy ? "占用中(测试进行时网页暂停采集)" : "空闲";
  $("btn-snapshot").disabled = !status.capture.can_capture;
}

async function takeSnapshot() {
  const res = await fetch("/api/v1/snapshots", {
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
  $("tree-time").textContent = data.tree_captured_at ?? "";
  $("tree-meta").textContent = data.tree_error
    ? `UI 树不可用:${data.tree_error}(原生拾取禁用,图像定位不受影响)`
    : data.consistent
      ? `${data.width}x${data.height} · ${data.orientation} · 可拾取`
      : `${data.width}x${data.height} · 截图与树间隔超限,画面可能不一致,拾取已禁用;请重新采集`;
  $("preview").src = `/api/v1/snapshots/${data.snapshot_id}/image`;
  $("preview-time").textContent = data.captured_at;
  if (!data.tree_error) {
    const treeRes = await fetch(`/api/v1/snapshots/${data.snapshot_id}/tree`);
    if (treeRes.ok) {
      state.treeNodes = (await treeRes.json()).nodes;
      renderTree();
    }
  } else {
    state.treeNodes = [];
    $("tree").textContent = "";
  }
  clearOverlay();
}

/* ── UI 树与双向拾取 ─────────────────────────────────────── */

function renderTree() {
  const ul = $("tree");
  ul.textContent = "";
  for (const node of state.treeNodes) {
    if (!node.text && !node.resource_id && !node.content_desc) continue;
    const li = document.createElement("li");
    li.className = node.clickable ? "clickable" : "";
    li.dataset.index = node.index;
    li.textContent =
      [node.class_name.split(".").pop(), node.resource_id, node.text, node.content_desc]
        .filter(Boolean)
        .join(" · ") + (node.bounds ? `  [${node.bounds.join(",")}]` : "");
    li.addEventListener("click", () => {
      highlightNode(node);
      drawBox(node.bounds, "tree-box");
    });
    ul.appendChild(li);
  }
}

function highlightNode(node) {
  for (const li of $("tree").children) {
    li.classList.toggle("selected", li.dataset.index == node.index);
  }
}

function drawBox(bounds, cls) {
  clearOverlay(cls);
  const img = $("preview");
  if (!img.naturalWidth) return;
  const scale = img.clientWidth / img.naturalWidth;
  const box = document.createElement("div");
  box.className = cls || "highlight";
  box.style.left = `${bounds[0] * scale}px`;
  box.style.top = `${bounds[1] * scale}px`;
  box.style.width = `${(bounds[2] - bounds[0]) * scale}px`;
  box.style.height = `${(bounds[3] - bounds[1]) * scale}px`;
  $("overlay").appendChild(box);
}

function clearOverlay(cls) {
  for (const box of [...$("overlay").children]) {
    if (!cls || box.className === cls) box.remove();
  }
}

/* 浏览器缩放/留白:CSS 坐标 → 截图像素(按 naturalWidth/clientWidth 比例换算) */
function toPixelCoords(event) {
  const img = $("preview");
  const rect = img.getBoundingClientRect();
  const scale = img.naturalWidth / rect.width;
  return {
    x: Math.round((event.clientX - rect.left) * scale),
    y: Math.round((event.clientY - rect.top) * scale),
  };
}

async function pickAt(event) {
  if (!state.snapshotId) {
    $("pick-hint").textContent = "请先采集快照。";
    return;
  }
  if (!state.consistent) {
    $("pick-hint").textContent = "快照不一致或无 UI 树,拾取已禁用;请重新采集。";
    return;
  }
  const { x, y } = toPixelCoords(event);
  const res = await fetch("/api/v1/pick", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ snapshot_id: state.snapshotId, x, y }),
  });
  const data = await res.json();
  if (!res.ok) {
    $("pick-hint").textContent =
      res.status === 404 ? "快照已过期,请重新采集后再拾取。" : `拾取失败(${data.code}):${data.message}`;
    return;
  }
  $("pick-point").textContent = `@ (${x}, ${y})${data.overlap ? " · 存在重叠,请选择" : ""}`;
  renderCandidates(data.candidates);
}

function renderCandidates(nodeCandidates) {
  const ul = $("candidate-list");
  ul.textContent = "";
  state.pickedLocator = null;
  $("btn-validate").disabled = true;
  $("btn-copy").disabled = true;
  $("expression").textContent = "";
  $("validate-note").textContent = "";
  for (const node of nodeCandidates) {
    for (const loc of node.locators) {
      const li = document.createElement("li");
      li.className = "candidate";
      li.textContent = `[${loc.by}] ${loc.value}  ${loc.risk !== "stable" ? "⚠ " + loc.risk : ""}`;
      li.addEventListener("click", () => selectCandidate(li, loc, node));
      ul.appendChild(li);
    }
  }
  if (!nodeCandidates.length) {
    const li = document.createElement("li");
    li.textContent = "该点未命中任何节点。";
    ul.appendChild(li);
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

/* ── 定位验证 ────────────────────────────────────────────── */

async function validatePicked() {
  if (!state.pickedLocator) return;
  $("validate-note").textContent = "验证中(重新采集查询)…";
  const res = await fetch("/api/v1/locators/validate", {
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
  $("preview").src = `/api/v1/snapshots/${data.snapshot_id}/image`;
  for (const m of data.matches) drawBox([m.x, m.y, m.x + m.width, m.y + m.height], "match-box");
  $("validate-note").textContent = `匹配 ${data.count} 个 · ${data.note}`;
}

/* ── 标签页 ──────────────────────────────────────────────── */

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

/* ── 预览 WS ─────────────────────────────────────────────── */

function connectPreview() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/api/v1/preview`);
  ws.onmessage = (event) => {
    const frame = JSON.parse(event.data);
    $("freshness").textContent = `帧时间 ${frame.captured_at}`;
    if (frame.kind === "frame") {
      $("preview").src = `data:image/jpeg;base64,${frame.jpeg_base64}`;
      $("preview-time").textContent = frame.captured_at;
      $("preview-message").textContent = "";
    } else {
      $("preview-message").textContent = frame.message || "预览暂不可用";
    }
  };
  ws.onclose = () => setTimeout(connectPreview, 2000);
}

/* ── 图像模板制作(工单 11)──────────────────────────────── */

const imageState = { region: null, logical: null, sha256: null };

function imageTabActive() {
  return document.querySelector(".tab.active").dataset.tab === "image";
}

function setupMarquee() {
  const img = $("preview");
  let start = null;
  img.addEventListener("mousedown", (event) => {
    if (!imageTabActive() || !state.snapshotId) return;
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
    const imgRect = img.getBoundingClientRect();
    const scale = img.clientWidth / img.naturalWidth;
    const x0 = Math.min(start.x, now.x), y0 = Math.min(start.y, now.y);
    const x1 = Math.max(start.x, now.x), y1 = Math.max(start.y, now.y);
    box.style.left = `${imgRect.left - imgRect.left + (x0 * scale)}px`;
    box.style.top = `${(y0 * scale)}px`;
    box.style.width = `${(x1 - x0) * scale}px`;
    box.style.height = `${(y1 - y0) * scale}px`;
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
    $("image-hint").textContent = `已框选 (${region.join(", ")});填写模板名后保存。`;
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
  const res = await fetch("/api/v1/templates", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (res.status === 409) {
    // 同名存在:请求旧摘要走确认更新
    const listRes = await fetch("/api/v1/templates");
    const known = (await listRes.json()).templates.find((t) => t.logical === logical);
    if (!known) { $("tpl-result").textContent = `保存失败(${data.code}):${data.message}`; return; }
    if (!confirm(`模板 ${logical} 已存在,基于旧版本确认更新?`)) {
      $("tpl-result").textContent = "已取消更新。";
      return;
    }
    const upRes = await fetch(`/api/v1/templates/${encodeURIComponent(logical)}`, {
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
  const parts = [`" ${imageState.logical}"`.replace('" ', '"')];
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
  const res = await fetch("/api/v1/templates/validate", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ target }),
  });
  const data = await res.json();
  if (!res.ok) { $("tpl-result").textContent = `验证失败(${data.code}):${data.message}`; return; }
  state.snapshotId = data.snapshot_id;
  $("preview").src = `/api/v1/snapshots/${data.snapshot_id}/image`;
  for (const m of data.matches) {
    drawBox([m.x, m.y, m.x + m.width, m.y + m.height], "match-box");
  }
  const scoreText = data.scores ? ` 分数 ${data.scores.map((s) => s.toFixed(3)).join(", ")}` : "";
  $("tpl-result").textContent = `匹配 ${data.count} 个 · ${data.note}.${scoreText}`;
}

$("btn-snapshot").addEventListener("click", takeSnapshot);
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
$("btn-validate").addEventListener("click", validatePicked);
$("btn-copy").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("expression").textContent);
  $("btn-copy").textContent = "已复制";
  setTimeout(() => ($("btn-copy").textContent = "复制表达式"), 1200);
});
$("preview").addEventListener("click", pickAt);
setupTabs();
refreshStatus();
setInterval(refreshStatus, 3000);
connectPreview();
