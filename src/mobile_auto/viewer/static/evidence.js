/* 失败证据浏览(P2 工单 08):只读清单 + 详情;顶层 调试/证据 视图切换。 */
"use strict";

const DEBUG_PANES = ["preview-pane", "inspect-pane", "tree-pane"];

function switchView(view) {
  document.body.classList.toggle("evidence-mode", view === "evidence");
  for (const button of document.querySelectorAll("#view-tabs .tab")) {
    button.classList.toggle("active", button.dataset.view === view);
  }
  if (view === "evidence") loadEvidenceList();
}

async function loadEvidenceList() {
  const list = document.getElementById("evidence-list");
  list.textContent = "";
  try {
    const res = await fetch("/api/v1/evidence");
    if (!res.ok) throw new Error(String(res.status));
    const data = await res.json();
    document.getElementById("evidence-count").textContent =
      data.evidence.length ? `${data.evidence.length} 条` : "";
    if (!data.evidence.length) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "暂无失败证据(--mobile-evidence 采集后在此浏览)。";
      list.appendChild(li);
      return;
    }
    for (const item of data.evidence) {
      const li = document.createElement("li");
      const created = (item.created_at ?? "").replace("T", " ").slice(0, 19);
      li.textContent = `${created} · ${item.device ?? "?"} · ${item.failure ?? "失败"} · ${item.frame_source ?? "-"}`;
      li.addEventListener("click", () => showEvidenceDetail(item.evidence_id));
      list.appendChild(li);
    }
  } catch {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "证据清单加载失败。";
    list.appendChild(li);
  }
}

async function showEvidenceDetail(evidenceId) {
  const detail = document.getElementById("evidence-detail");
  const image = document.getElementById("evidence-frame");
  const attrBody = document.querySelector("#evidence-attr tbody");
  const files = document.getElementById("evidence-files");
  detail.classList.remove("hidden");
  image.classList.add("hidden");
  attrBody.textContent = "";
  files.textContent = "";
  try {
    const res = await fetch(`/api/v1/evidence/${evidenceId}`);
    if (!res.ok) throw new Error(String(res.status));
    const meta = await res.json();
    const rows = [
      ["证据", meta.evidence_id],
      ["时间", meta.created_at],
      ["设备", (meta.device ?? {}).serial ?? "?"],
      ["App", (meta.device ?? {}).app_package || "-"],
      ["失败", `${(meta.failure ?? {}).type ?? "?"}:${(meta.failure ?? {}).message ?? ""}`],
      ["画面来源", (meta.frame_source ?? {}).source ?? "-"],
    ];
    for (const [key, value] of rows) {
      const tr = document.createElement("tr");
      const th = document.createElement("th");
      const td = document.createElement("td");
      th.textContent = key;
      td.textContent = value ?? "-";
      tr.append(th, td);
      attrBody.appendChild(tr);
    }
    for (const artifact of meta.artifacts ?? []) {
      if (artifact.kind === "post-failure-frame" || artifact.name.endsWith(".png")) {
        image.src = `/api/v1/evidence/${evidenceId}/files/${encodeURIComponent(artifact.name)}`;
        image.classList.remove("hidden");
      }
      const li = document.createElement("li");
      const link = document.createElement("a");
      link.href = `/api/v1/evidence/${evidenceId}/files/${encodeURIComponent(artifact.name)}`;
      link.target = "_blank";
      link.textContent = artifact.name;
      li.append(link, ` (${artifact.kind})`);
      files.appendChild(li);
    }
    for (const missed of meta.not_captured ?? []) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = `未采集 ${missed.kind}:${missed.reason ?? ""}`;
      files.appendChild(li);
    }
  } catch {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "证据详情加载失败。";
    files.appendChild(li);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  for (const button of document.querySelectorAll("#view-tabs .tab")) {
    button.addEventListener("click", () => switchView(button.dataset.view));
  }
});
