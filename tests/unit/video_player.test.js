/* 视频帧解析纯函数单测:node tests/unit/video_player.test.js 运行。
   覆盖 27B 线格式解析与 codec 串推导,布局与服务端
   src/mobile_auto/viewer/videostream.py 一致。 */
"use strict";

const assert = require("assert");
const {
  HEADER_BYTES,
  KIND,
  codecFromAnnexB,
  parseRelayHeader,
  relayPayload,
} = require("../../src/mobile_auto/viewer/static/video_player.js");

let passed = 0;
async function test(name, fn) {
  await fn();
  passed += 1;
  console.log(`ok - ${name}`);
}

function packHeader(
  kind,
  {
    generation = 1,
    sequence = 0,
    ptsUs = 0n,
    key = false,
    width = 0,
    height = 0,
    payloadLength = 0,
  } = {},
) {
  const buf = new ArrayBuffer(HEADER_BYTES);
  const view = new DataView(buf);
  view.setUint8(0, 1);
  view.setUint8(1, kind);
  view.setUint32(2, generation);
  view.setUint32(6, sequence);
  view.setBigUint64(10, ptsUs);
  view.setUint8(18, key ? 1 : 0);
  view.setUint16(19, width);
  view.setUint16(21, height);
  view.setUint32(23, payloadLength);
  return buf;
}

function frameMessage(headerOpts, payload) {
  const message = new Uint8Array(HEADER_BYTES + payload.length);
  message.set(new Uint8Array(packHeader(KIND.FRAME, headerOpts)), 0);
  message.set(payload, HEADER_BYTES);
  return message.buffer;
}

(async () => {
  await test("解析 frame 包头(网络字节序各字段)与载荷切片", async () => {
    const payload = new Uint8Array([9, 8, 7]);
    const buffer = frameMessage(
      {
        generation: 42,
        sequence: 5,
        ptsUs: 1234n,
        key: true,
        width: 1080,
        height: 2400,
        payloadLength: 3,
      },
      payload,
    );
    const parsed = parseRelayHeader(buffer);
    assert.strictEqual(parsed.kind, KIND.FRAME);
    assert.strictEqual(parsed.generation, 42);
    assert.strictEqual(parsed.sequence, 5);
    assert.strictEqual(parsed.ptsUs, 1234n);
    assert.strictEqual(parsed.key, true);
    assert.strictEqual(parsed.width, 1080);
    assert.strictEqual(parsed.height, 2400);
    assert.strictEqual(parsed.payloadLength, 3);
    assert.deepStrictEqual([...relayPayload(buffer, parsed)], [9, 8, 7]);
  });

  await test("短包/坏版本/坏 kind/超限与不完整载荷拒绝", async () => {
    assert.throws(() => parseRelayHeader(new ArrayBuffer(10)));
    const badVersion = packHeader(KIND.FRAME, {});
    new DataView(badVersion).setUint8(0, 9);
    assert.throws(() => parseRelayHeader(badVersion));
    assert.throws(() => parseRelayHeader(packHeader(9, {})));
    assert.throws(() =>
      parseRelayHeader(packHeader(KIND.FRAME, { payloadLength: 3 * 1024 * 1024 })),
    );
    assert.throws(() =>
      parseRelayHeader(packHeader(KIND.FRAME, { payloadLength: 5 })),
    );
  });

  await test("Annex B SPS → avc1.PPCCLL codec 串;无 SPS 返回 null", async () => {
    const sps = new Uint8Array([
      0, 0, 0, 1, 0x67, 0x64, 0x00, 0x28, 0xac, 0xd9, 0x40, 0x50, 0, 0, 1, 0x68,
      0xeb, 0xec, 0xb2, 0x2c,
    ]);
    assert.strictEqual(codecFromAnnexB(sps), "avc1.640028");
    assert.strictEqual(codecFromAnnexB(new Uint8Array([1, 2, 3, 4, 5, 6])), null);
  });

  console.log(`${passed} tests passed`);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
