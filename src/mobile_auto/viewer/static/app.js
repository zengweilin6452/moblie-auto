/* mobile-auto 查看器:实时预览与 UI 树;仅观察,不控制设备。 */
"use strict";

const $ = (id) => document.getElementById(id);

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
  $("tree-time").textContent = data.tree_captured_at ?? "";
  if (data.tree_error || data.consistent === false) {
    $("tree-meta").textContent = data.tree_error
      ? `UI 树不可用:${data.tree_error}`
      : "截图与树采集间隔超限,画面可能不一致,原生拾取已禁用";
  } else {
    $("tree-meta").textContent = `${data.width}x${data.height} · ${data.orientation}`;
  }
  const treeRes = await fetch(`/api/v1/snapshots/${data.snapshot_id}/tree`);
  if (treeRes.ok) {
    const tree = await treeRes.json();
    renderTree(tree.nodes);
    $("preview").src = `/api/v1/snapshots/${data.snapshot_id}/image`;
    $("preview-time").textContent = data.captured_at;
  }
}

function renderTree(nodes) {
  const byIndex = new Map(nodes.map((n) => [n.index, n]));
  const ul = $("tree");
  ul.textContent = "";
  for (const node of nodes) {
    if (!node.text && !node.resource_id && !node.content_desc) continue;
    const li = document.createElement("li");
    li.className = node.clickable ? "clickable" : "";
    const label = [node.class_name.split(".").pop(), node.resource_id, node.text, node.content_desc]
      .filter(Boolean)
      .join(" · ");
    li.textContent = label + (node.bounds ? `  [${node.bounds.join(",")}]` : "");
    ul.appendChild(li);
  }
  void byIndex;
}

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
      $("preview-message").textContent = frame.message || frame.message_placeholder || frame.code || "预览暂不可用";
    }
  };
  ws.onclose = () => setTimeout(connectPreview, 2000);
}

$("btn-snapshot").addEventListener("click", takeSnapshot);
refreshStatus();
setInterval(refreshStatus, 3000);
connectPreview();
