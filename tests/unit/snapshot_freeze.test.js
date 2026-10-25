/* 冻结引用状态机单测:node tests/unit/snapshot_freeze.test.js 运行。
 * 覆盖 viewer 编辑底图的不变量:至多一份冻结引用;换底图先释放旧的;
 * 冻结失败不登记;释放尽力而为。 */
"use strict";

const assert = require("assert");
const {
  createSnapshotFreeze,
} = require("../../src/mobile_auto/viewer/static/snapshot_freeze.js");

let passed = 0;
async function test(name, fn) {
  await fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function recordingPost(calls, failOn) {
  return async (id, frozen) => {
    calls.push([id, frozen]);
    const fail = (failOn || []).find(([fid, ffrozen]) => fid === id && ffrozen === frozen);
    if (fail) throw new Error(`post failed: ${id} frozen=${frozen}`);
  };
}

(async () => {
  await test("首次冻结只发一次 frozen:true", async () => {
    const calls = [];
    const ctl = createSnapshotFreeze(recordingPost(calls));
    await ctl.freeze("s1");
    assert.deepStrictEqual(calls, [["s1", true]]);
    assert.strictEqual(ctl.current, "s1");
  });

  await test("换底图先释放旧引用再冻结新引用(顺序保证容量让位)", async () => {
    const calls = [];
    const ctl = createSnapshotFreeze(recordingPost(calls));
    await ctl.freeze("s1");
    await ctl.freeze("s2");
    assert.deepStrictEqual(calls, [["s1", true], ["s1", false], ["s2", true]]);
    assert.strictEqual(ctl.current, "s2");
  });

  await test("冻结失败不登记引用,旧引用已释放", async () => {
    const calls = [];
    const ctl = createSnapshotFreeze(recordingPost(calls, [["s2", true]]));
    await ctl.freeze("s1");
    await assert.rejects(() => ctl.freeze("s2"));
    assert.deepStrictEqual(calls, [["s1", true], ["s1", false], ["s2", true]]);
    assert.strictEqual(ctl.current, null);
  });

  await test("release 发出解冻并清空;无引用时幂等", async () => {
    const calls = [];
    const ctl = createSnapshotFreeze(recordingPost(calls));
    await ctl.freeze("s1");
    await ctl.release();
    await ctl.release();
    assert.deepStrictEqual(calls, [["s1", true], ["s1", false]]);
    assert.strictEqual(ctl.current, null);
  });

  await test("释放失败被吞掉,不阻断回实时", async () => {
    const calls = [];
    const ctl = createSnapshotFreeze(recordingPost(calls, [["s1", false]]));
    await ctl.freeze("s1");
    await ctl.release();
    assert.strictEqual(ctl.current, null);
  });

  await test("同 id 重复冻结不先解冻自己", async () => {
    const calls = [];
    const ctl = createSnapshotFreeze(recordingPost(calls));
    await ctl.freeze("s1");
    await ctl.freeze("s1");
    assert.deepStrictEqual(calls, [["s1", true], ["s1", true]]);
  });

  await test("freeze(null) 只释放不冻结", async () => {
    const calls = [];
    const ctl = createSnapshotFreeze(recordingPost(calls));
    await ctl.freeze("s1");
    await ctl.freeze(null);
    assert.deepStrictEqual(calls, [["s1", true], ["s1", false]]);
    assert.strictEqual(ctl.current, null);
  });

  console.log(`\n${passed} tests passed`);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
