"""图像匹配引擎基准与校准工具(image-v2)。

子命令:
  phases   合成 1080x2340 截图的分阶段耗时(性能预算依据)
  samples  真实样本在各匹配位置的门分数(颜色门校准依据)
  crossres 跨分辨率(有界多尺度)路径耗时与命中
  prior    采集位置先验:全屏 vs 先验命中 vs 未命中回退(P4b)

用法:.venv/Scripts/python.exe scripts/bench_image.py <子命令>
验收记录引用本脚本输出;不做 CI 时间断言(易抖动)。
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time

import cv2
import numpy as np

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from mobile_auto.imaging import engine as img  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "images"


def png_bytes(array_rgb: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", array_rgb[:, :, ::-1])
    assert ok
    return buf.tobytes()


def bench_phases() -> None:
    rng = np.random.default_rng(7)
    screen = cv2.GaussianBlur(
        rng.integers(0, 255, size=(2340, 1080, 3), dtype=np.uint8), (0, 0), 3.0
    )
    patch = screen[800:896, 500:596].copy()
    shot_png, tpl_png = png_bytes(screen), png_bytes(patch)

    shot = img.decode_rgb(shot_png, what="截图")
    gray = cv2.cvtColor(shot, cv2.COLOR_RGB2GRAY)
    prepared = img.prepare_template(tpl_png)
    marks: list[tuple[str, float]] = []

    def tick(label: str, fn) -> None:
        t0 = time.perf_counter()
        result = fn()
        marks.append((label, time.perf_counter() - t0))
        return result

    scores = tick(
        "matchTemplate",
        lambda: cv2.matchTemplate(gray, prepared.gray, cv2.TM_CCOEFF_NORMED),
    )
    peaks = tick("峰值提取", lambda: img._local_peaks(scores, img.DEFAULT_CONFIDENCE))
    tick("NMS", lambda: img._non_max_suppress(peaks, 96, 96))

    t0 = time.perf_counter()
    img.match_template(shot_png, tpl_png)
    total = time.perf_counter() - t0

    print("截图 1080x2340 + 96x96 模板,端到端含 PNG 解码与模板准备:")
    for label, dt in marks:
        print(f"  {label:<16} {dt * 1000:7.1f} ms")
    print(f"  {'端到端':<16} {total * 1000:7.1f} ms")


def bench_samples() -> None:
    import json

    samples = json.loads((FIXTURES / "samples.json").read_text(encoding="utf-8"))
    header = (
        f"{'template':<22} {'screenshot':<26} {'expect':<9} "
        "gray峰  H      S      V      均值差  门"
    )
    print(header)
    for s in samples:
        shot = img.decode_rgb((FIXTURES / s["screenshot"]).read_bytes(), what="s")
        tpl = img.prepare_template((FIXTURES / s["template"]).read_bytes(), what="t")
        th, tw = tpl.rgb.shape[:2]
        scores = cv2.matchTemplate(
            cv2.cvtColor(shot, cv2.COLOR_RGB2GRAY), tpl.gray, cv2.TM_CCOEFF_NORMED
        )
        _, max_val, _, max_loc = cv2.minMaxLoc(scores)
        px, py = max_loc
        cand = shot[py : py + th, px : px + tw]
        cand_hsv = cv2.cvtColor(np.clip(cand, 10, 245), cv2.COLOR_RGB2HSV)
        nccs = [img._channel_ncc(cand_hsv[:, :, i], tpl.hsv[:, :, i]) for i in range(3)]
        diff = float(np.abs(cand.astype(np.int16) - tpl.rgb.astype(np.int16)).mean())
        gate = img._color_matches(tpl, cand, cand_hsv)
        verdict = "过" if gate else "拒"
        print(f"{s['template']:<22} {s['screenshot']:<26} {s['expect']:<9} "
              f"{max_val:.3f}  {nccs[0]:+.3f} {nccs[1]:+.3f} {nccs[2]:+.3f} "
              f"{diff:6.2f}  {verdict}")


def bench_crossres() -> None:
    shot = img.decode_rgb((FIXTURES / "page-main.png").read_bytes(), what="s")
    for name in ("t-wlan-row.png", "t-icon-wlan.png"):
        tpl = (FIXTURES / name).read_bytes()
        for factor in (0.85, 1.15, 1.25):
            big = cv2.resize(shot, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
            png = png_bytes(big)
            img.match_template(png, tpl, source_resolution=(1080, 2400))  # 预热
            t0 = time.perf_counter()
            runs = 5
            results = []
            for _ in range(runs):
                results = img.match_template(png, tpl, source_resolution=(1080, 2400))
            dt = (time.perf_counter() - t0) / runs * 1000
            print(f"{name} @ {factor}x({big.shape[1]}x{big.shape[0]}): "
                  f"{dt:.0f} ms/次, {len(results)} 结果")


def bench_prior() -> None:
    """P4b 采集位置先验:同分辨率下全屏搜索 vs 先验命中 vs 先验未命中回退。

    合成 1080x2340 截图 + 96x96 UI 形态模板,采集位置取屏幕右下象限,
    未命中场景把模板放在对角(预测区域外),命中场景含无先验对照。
    """
    rng = np.random.default_rng(11)
    screen = cv2.GaussianBlur(
        rng.integers(0, 255, size=(2340, 1080, 3), dtype=np.uint8), (0, 0), 3.0
    )
    center_region = (700, 1500, 96, 96)  # 采集位置:屏幕右下象限
    cx, cy = 700 + 48, 1500 + 48
    patch = screen[1500:1596, 700:796].copy()
    shot_png, tpl_png = png_bytes(screen), png_bytes(patch)
    capture_center = img.capture_center_from_region(center_region, (1080, 2340))

    def timed(fn, runs=5):
        fn()  # 预热
        t0 = time.perf_counter()
        for _ in range(runs):
            result = fn()
        return (time.perf_counter() - t0) / runs * 1000, result

    full_ms, full = timed(
        lambda: img.match_template(shot_png, tpl_png)
    )
    prior_ms, prior = timed(
        lambda: img.match_template(shot_png, tpl_png, capture_center=capture_center)
    )

    far = cv2.GaussianBlur(
        rng.integers(0, 255, size=(2340, 1080, 3), dtype=np.uint8), (0, 0), 3.0
    )
    far[300:396, 100:196] = patch  # 对角:预测区域外,先验必未命中
    far_png = png_bytes(far)
    fallback_ms, fallback = timed(
        lambda: img.match_template(far_png, tpl_png, capture_center=capture_center)
    )

    print(f"截图 1080x2340 + 96x96 模板,采集中心 ({cx},{cy}):")
    print(f"  无先验全屏        {full_ms:7.1f} ms/次,{len(full)} 结果")
    print(f"  先验命中          {prior_ms:7.1f} ms/次,{len(prior)} 结果")
    print(f"  先验未命中(回退) {fallback_ms:7.1f} ms/次,{len(fallback)} 结果")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["phases", "samples", "crossres", "prior"]
    )
    match parser.parse_args().command:
        case "phases":
            bench_phases()
        case "samples":
            bench_samples()
        case "crossres":
            bench_crossres()
        case "prior":
            bench_prior()


if __name__ == "__main__":
    main()
