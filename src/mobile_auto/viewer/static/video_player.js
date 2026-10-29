/* 浏览器直解 H.264 播放器(web-preview 03)。
   线格式与服务端 viewer/videostream.py 对齐:27 字节网络字节序包头 +
   载荷;kind 1=config(Annex B SPS/PPS)、2=session(空载荷,尺寸在包头)、
   3=frame(完整访问单元)。纯解析函数与浏览器播放工厂分开,前者可在
   node 单测中直接验证。 */
"use strict";

(function (root) {
  const HEADER_BYTES = 27;
  const KIND = { CONFIG: 1, SESSION: 2, FRAME: 3 };
  const PROTOCOL_VERSION = 1;
  const MAX_PAYLOAD = 2 * 1024 * 1024;
  const BACKPRESSURE_QUEUE = 8; // 解码积压阈值:超出丢 delta 保实时

  /* ArrayBuffer → 包头字段;长度/版本/kind 异常如实抛错(调用方回退) */
  function parseRelayHeader(buffer) {
    if (buffer.byteLength < HEADER_BYTES) throw new Error("包头不完整");
    const view = new DataView(buffer);
    const version = view.getUint8(0);
    if (version !== PROTOCOL_VERSION) throw new Error(`协议版本不符:${version}`);
    const kind = view.getUint8(1);
    if (kind !== KIND.CONFIG && kind !== KIND.SESSION && kind !== KIND.FRAME) {
      throw new Error(`未知 kind:${kind}`);
    }
    const header = {
      version,
      kind,
      generation: view.getUint32(2),
      sequence: view.getUint32(6),
      ptsUs: view.getBigUint64(10),
      key: (view.getUint8(18) & 0x01) !== 0,
      width: view.getUint16(19),
      height: view.getUint16(21),
      payloadLength: view.getUint32(23),
    };
    if (header.payloadLength > MAX_PAYLOAD) throw new Error("载荷超限");
    if (buffer.byteLength < HEADER_BYTES + header.payloadLength) {
      throw new Error("载荷不完整");
    }
    return header;
  }

  function relayPayload(buffer, header) {
    return new Uint8Array(buffer, HEADER_BYTES, header.payloadLength);
  }

  /* Annex B 里 SPS(起始码后 nal_unit_type=7)的三字节
     profile/constraint/level → WebCodecs codec 串 avc1.PPCCLL */
  function codecFromAnnexB(payload) {
    const bytes = payload instanceof Uint8Array ? payload : new Uint8Array(payload);
    for (let i = 0; i + 6 < bytes.length; i++) {
      if (bytes[i] === 0 && bytes[i + 1] === 0 && bytes[i + 2] === 1) {
        if ((bytes[i + 3] & 0x1f) === 7) {
          const hex = (b) => b.toString(16).padStart(2, "0");
          return `avc1.${hex(bytes[i + 4])}${hex(bytes[i + 5])}${hex(bytes[i + 6])}`;
        }
      }
    }
    return null;
  }

  /* 浏览器播放器:WS 二进制 → WebCodecs VideoDecoder → canvas。
     config 载荷与首个关键帧数据 concat(Annex B 无 description 模式);
     解码积压丢 delta 保实时;任何失败经 onState("error") 交调用方回退。 */
  function createH264Player(options) {
    const opts = options || {};
    let ws = null;
    let decoder = null;
    let configured = false;
    let pendingConfig = null;
    let closed = false;

    function notify(kind, message) {
      if (opts.onState) opts.onState(kind, message);
    }

    function drawFrame(frame) {
      const canvas = opts.canvas;
      if (canvas) {
        if (canvas.width !== frame.displayWidth) canvas.width = frame.displayWidth;
        if (canvas.height !== frame.displayHeight) canvas.height = frame.displayHeight;
        canvas.getContext("2d").drawImage(frame, 0, 0);
      }
      frame.close();
      if (opts.onFrame) opts.onFrame();
    }

    function ensureDecoder(codec) {
      if (typeof VideoDecoder === "undefined") {
        throw new Error("浏览器不支持 WebCodecs");
      }
      if (decoder) return;
      decoder = new VideoDecoder({
        output: drawFrame,
        error: (err) => fail(`解码器错误:${err.message}`),
      });
      decoder.configure({ codec });
      configured = true;
    }

    function handleBinary(buffer) {
      const header = parseRelayHeader(buffer);
      const payload = relayPayload(buffer, header);
      if (header.kind === KIND.SESSION) {
        if (opts.onSession) opts.onSession({ w: header.width, h: header.height });
        return;
      }
      if (header.kind === KIND.CONFIG) {
        pendingConfig = Uint8Array.from(payload);
        return;
      }
      if (!header.key) {
        if (!configured) return; // 关键帧同步前的 delta(订阅门控兜底)
        if (decoder.decodeQueueSize > BACKPRESSURE_QUEUE) return; // 背压丢 delta
      }
      let data = payload;
      if (header.key) {
        if (!configured) ensureDecoder(codecFromAnnexB(payload) || "avc1.42E01E");
        if (pendingConfig) {
          const merged = new Uint8Array(pendingConfig.length + payload.length);
          merged.set(pendingConfig, 0);
          merged.set(payload, pendingConfig.length);
          data = merged;
          pendingConfig = null;
        }
      }
      decoder.decode(
        new EncodedVideoChunk({
          type: header.key ? "key" : "delta",
          timestamp: Number(header.ptsUs),
          data,
        }),
      );
    }

    function fail(message) {
      close();
      notify("error", message);
    }

    function open(deviceQs) {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(
        `${proto}://${location.host}/api/v1/preview/video${deviceQs || ""}`,
      );
      ws.binaryType = "arraybuffer";
      ws.onopen = () => ws.send(JSON.stringify({ proto: 1 }));
      ws.onmessage = (event) => {
        if (typeof event.data === "string") {
          const msg = JSON.parse(event.data);
          if (msg.kind === "ready") notify("ready", msg);
          else if (msg.kind === "error") fail(msg.message || msg.code);
          return;
        }
        try {
          handleBinary(event.data);
        } catch (err) {
          fail(`码流处理失败:${err.message}`);
        }
      };
      ws.onclose = () => {
        if (!closed) {
          closed = true;
          notify("closed", "视频信道关闭");
        }
      };
    }

    function close() {
      closed = true;
      if (ws) {
        const s = ws;
        ws = null;
        s.onclose = null;
        s.onmessage = null;
        s.close();
      }
      if (decoder) {
        try {
          decoder.close();
        } catch {
          /* 已关闭 */
        }
        decoder = null;
      }
      configured = false;
      pendingConfig = null;
    }

    return {
      open,
      close,
      get active() {
        return ws !== null;
      },
    };
  }

  const api = {
    BACKPRESSURE_QUEUE,
    HEADER_BYTES,
    KIND,
    MAX_PAYLOAD,
    PROTOCOL_VERSION,
    codecFromAnnexB,
    createH264Player,
    parseRelayHeader,
    relayPayload,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.MobileVideo = api;
})(typeof window !== "undefined" ? window : null);
