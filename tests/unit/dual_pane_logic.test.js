/* 双栏联动纯函数单测(工单 05):node tests/unit/dual_pane_logic.test.js 运行。 */
"use strict";

const assert = require("assert");
const {
  clamp01,
  eventToPixel,
  displayScale,
  hitTest,
  isStale,
} = require("../../src/mobile_auto/viewer/static/coords.js");

let passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

test("clamp01 夹限", () => {
  assert.strictEqual(clamp01(-0.2), 0);
  assert.strictEqual(clamp01(1.5), 1);
  assert.strictEqual(clamp01(0.4), 0.4);
});

test("eventToPixel 事件坐标按参照空间换算", () => {
  const rect = { left: 10, top: 20, width: 244, height: 540 }; // 显示一半(488x1080 的一半)
  const event = { clientX: 10 + 122, clientY: 20 + 270 }; // 显示中心
  const pixel = eventToPixel(event, rect, 488, 1080); // 帧空间
  assert.deepStrictEqual(pixel, { x: 244, y: 540 });
  // 设备空间参照(1080x2400):同一点对应设备中心
  assert.deepStrictEqual(eventToPixel(event, rect, 1080, 2400), {
    x: 540,
    y: 1200,
  });
});

test("displayScale 显示宽度到参照空间比例", () => {
  assert.strictEqual(displayScale(244, 488), 0.5);
});

test("hitTest 包含点按面积升序,重叠由用户选择", () => {
  const nodes = [
    { index: 0, bounds: [0, 0, 1080, 2400] },
    { index: 1, bounds: [100, 200, 500, 600] },
    { index: 2, bounds: [120, 220, 200, 300] }, // 最小,应排最前
    { index: 3, bounds: [700, 700, 900, 900] }, // 不包含
  ];
  const hits = hitTest(nodes, 150, 250);
  assert.deepStrictEqual(
    hits.map((n) => n.index),
    [2, 1, 0],
  );
  assert.deepStrictEqual(hitTest(nodes, 800, 800).map((n) => n.index), [3, 0]);
  assert.deepStrictEqual(hitTest(nodes, 2000, 2000), []);
});

test("hitTest 越界 bounds 的节点被忽略", () => {
  const nodes = [{ index: 0, bounds: null }, { index: 1, bounds: [0, 0, 10, 10] }];
  assert.deepStrictEqual(hitTest(nodes, 5, 5).map((n) => n.index), [1]);
});

test("isStale 时间新鲜度判定", () => {
  const now = Date.parse("2026-10-24T10:00:05Z");
  assert.strictEqual(isStale("2026-10-24T10:00:00Z", now, 3000), true);
  assert.strictEqual(isStale("2026-10-24T10:00:03Z", now, 3000), false);
  assert.strictEqual(isStale(null, now, 3000), true);
  assert.strictEqual(isStale("not-a-time", now, 3000), true);
});

console.log(`\n${passed} tests passed`);
