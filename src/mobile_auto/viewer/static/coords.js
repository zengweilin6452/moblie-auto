/* 坐标换算与命中测试纯函数(工单 05):浏览器与 node 单测共用。 */
"use strict";

(function (root) {
  function clamp01(value) {
    return Math.min(Math.max(value, 0), 1);
  }

  /* 浏览器事件坐标 → 参照空间像素;x/y 各用对应维度比例(显示区
     宽高取整会引入纵横比微差,按维换算更准) */
  function eventToPixel(event, rect, spaceWidth, spaceHeight) {
    return {
      x: Math.round((event.clientX - rect.left) * (spaceWidth / rect.width)),
      y: Math.round((event.clientY - rect.top) * (spaceHeight / rect.height)),
    };
  }

  /* 显示比例:显示宽度 → 参照空间(设备像素或帧像素) */
  function displayScale(clientWidth, spaceWidth) {
    return clientWidth / spaceWidth;
  }

  /* 设备像素命中测试:包含点且面积升序(小在上),返回节点数组 */
  function hitTest(nodes, x, y) {
    const hits = nodes.filter(
      (node) =>
        node.bounds &&
        node.bounds[0] <= x &&
        x < node.bounds[2] &&
        node.bounds[1] <= y &&
        y < node.bounds[3],
    );
    hits.sort(
      (a, b) =>
        (a.bounds[2] - a.bounds[0]) * (a.bounds[3] - a.bounds[1]) -
        (b.bounds[2] - b.bounds[0]) * (b.bounds[3] - b.bounds[1]),
    );
    return hits;
  }

  /* 树快照是否过期(毫秒);无时间视为过期 */
  function isStale(capturedAtIso, nowMs, maxAgeMs) {
    if (!capturedAtIso) return true;
    const at = Date.parse(capturedAtIso);
    if (Number.isNaN(at)) return true;
    return nowMs - at > maxAgeMs;
  }

  const api = { clamp01, eventToPixel, displayScale, hitTest, isStale };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.MobileCoords = api;
})(typeof window !== "undefined" ? window : null);
