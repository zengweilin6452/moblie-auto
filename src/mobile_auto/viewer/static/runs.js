/* 设备占用与运行态(P2 工单 07):/leases + /runs 只读联动呈现。 */
"use strict";

const OCCUPANCY_REFRESH_MS = 5000;

function fmtHolder(holder) {
  if (!holder) return "";
  const who = holder.worker || holder.hostname || "?";
  return holder.purpose ? `${who}(${holder.purpose})` : who;
}

async function refreshOccupancy() {
  if (document.body.classList.contains("evidence-mode")) return; // 证据视图不刷新
  const body = document.querySelector("#occupancy-table tbody");
  if (!body) return;
  let leases = [];
  let runs = [];
  try {
    const [leaseRes, runRes] = await Promise.all([
      fetch("/api/v1/leases"),
      fetch("/api/v1/runs"),
    ]);
    if (leaseRes.ok) leases = (await leaseRes.json()).leases ?? [];
    if (runRes.ok) runs = (await runRes.json()).runs ?? [];
  } catch {
    return; // 只读展示:拉取失败保留上一帧,不报错
  }
  const runBySerial = new Map();
  for (const run of runs) {
    if (run.serial && !run.stale && run.phase === "running") {
      runBySerial.set(run.serial, run);
    }
  }
  body.textContent = "";
  for (const lease of leases) {
    const run = runBySerial.get(lease.serial);
    const tr = document.createElement("tr");
    const serialTd = document.createElement("td");
    const holdTd = document.createElement("td");
    const testTd = document.createElement("td");
    serialTd.textContent = lease.serial;
    holdTd.textContent = lease.held
      ? `占用中 · ${fmtHolder(lease.holder)}`
      : "空闲";
    testTd.textContent = run ? `${run.worker}:${run.current_test ?? ""}` : "";
    tr.append(serialTd, holdTd, testTd);
    body.appendChild(tr);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  refreshOccupancy();
  setInterval(refreshOccupancy, OCCUPANCY_REFRESH_MS);
});
