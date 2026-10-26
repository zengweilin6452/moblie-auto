/* 失败证据浏览(P2 工单 08):只读清单 + 详情;顶层 调试/证据 视图切换。
   详情内再分「详情 / 动作流水」子页签(工单 01):动作流水读证据同 worker
   目录的 actions.jsonl,经独立端点反查,不走 /files 白名单。 */
"use strict";

const DEBUG_PANES = ["preview-pane", "inspect-pane", "tree-pane"];

let currentEvidenceId = null;
let currentEvidenceMeta = null;

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

function switchEvidenceTab(name) {
  for (const button of document.querySelectorAll("#evidence-subtabs .tab")) {
    button.classList.toggle("active", button.dataset.evidenceTab === name);
  }
  document
    .getElementById("evidence-tab-detail")
    .classList.toggle("hidden", name !== "detail");
  document
    .getElementById("evidence-tab-journal")
    .classList.toggle("hidden", name !== "journal");
  if (name === "journal") loadEvidenceJournal();
}

function formatJournalTarget(line) {
  const target = line.target;
  if (target && target.by !== undefined) {
    let text = `${target.by}=${target.value ?? ""}`;
    if (target.index !== undefined && target.index !== null) {
      text += ` [${target.index}]`;
    }
    return text;
  }
  if (Array.isArray(line.point)) return `(${line.point.join(", ")})`;
  if (Array.isArray(line.from) && Array.isArray(line.to)) {
    return `${line.from.join(",")} → ${line.to.join(",")}`;
  }
  if (line.scheme) return line.scheme;
  if (line.key) return `key=${line.key}`;
  if (line.direction) return `${line.direction} ${line.amount ?? ""}`.trim();
  return "-";
}

function formatJournalSummary(summary) {
  if (!summary) return "该证据没有动作流水摘要。";
  const total = Number(summary.total_duration_ms ?? 0).toFixed(1);
  const head = `共 ${summary.steps} 步 · 总耗时 ${total} ms`;
  const failure = summary.first_failure;
  if (!failure) return `${head} · 无失败步`;
  const detail = failure.error_type ? `: ${failure.error_type}` : "";
  return `${head} · 首个失败步 #${failure.step} ${failure.op} (${failure.outcome}${detail})`;
}

async function loadEvidenceJournal() {
  const body = document.querySelector("#evidence-journal tbody");
  const note = document.getElementById("evidence-journal-note");
  const summary = document.getElementById("evidence-journal-summary");
  body.textContent = "";
  note.textContent = "";
  summary.textContent = formatJournalSummary(currentEvidenceMeta?.journal_summary);
  if (!currentEvidenceId) return;
  try {
    const res = await fetch(`/api/v1/evidence/${currentEvidenceId}/journal`);
    if (!res.ok) throw new Error(String(res.status));
    const data = await res.json();
    let step = 0;
    for (const line of data.lines) {
      if (line.op === "journal") continue; // 截断标记不是一步
      step += 1;
      const tr = document.createElement("tr");
      const values = [
        String(step),
        (line.ts ?? "").replace("T", " ").slice(11, 23),
        line.op ?? "-",
        formatJournalTarget(line),
        line.duration_ms === undefined ? "-" : Number(line.duration_ms).toFixed(1),
        line.outcome ?? "-",
        line.channel ?? "-",
      ];
      for (const value of values) {
        const td = document.createElement("td");
        td.textContent = value;
        tr.appendChild(td);
      }
      body.appendChild(tr);
    }
    if (!step) note.textContent = "证据目录存在,但本次运行没有产生动作流水。";
    if (data.truncated) {
      note.textContent = `仅显示前 ${data.lines.length} 行(共 ${data.total} 行;文件已达上限或被裁剪)。`;
    }
  } catch {
    note.textContent = "动作流水加载失败(该证据未开启 --mobile-evidence 或没有记录)。";
  }
}

async function showEvidenceDetail(evidenceId) {
  const detail = document.getElementById("evidence-detail");
  const image = document.getElementById("evidence-frame");
  const attrBody = document.querySelector("#evidence-attr tbody");
  const files = document.getElementById("evidence-files");
  currentEvidenceId = evidenceId;
  currentEvidenceMeta = null;
  detail.classList.remove("hidden");
  image.classList.add("hidden");
  attrBody.textContent = "";
  files.textContent = "";
  switchEvidenceTab("detail");
  document.querySelector("#evidence-journal tbody").textContent = "";
  document.getElementById("evidence-journal-summary").textContent = "";
  document.getElementById("evidence-journal-note").textContent = "";
  try {
    const res = await fetch(`/api/v1/evidence/${evidenceId}`);
    if (!res.ok) throw new Error(String(res.status));
    const meta = await res.json();
    currentEvidenceMeta = meta;
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
  for (const button of document.querySelectorAll("#evidence-subtabs .tab")) {
    button.addEventListener("click", () =>
      switchEvidenceTab(button.dataset.evidenceTab),
    );
  }
});
