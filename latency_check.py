"""延迟体检：把「从字幕出现到看到中文」拆成每一段，逐段计时。

用法：python latency_check.py            （用 config.json 里的真实 key / 区域）
      python latency_check.py --no-llm   （只测本地：指纹 + OCR）

输出：
  指纹(probe)      画面变了没 —— 决定要不要 OCR，应该只有十几毫秒
  OCR              抓图 + 文字识别
  旧路径           一次调用同时要「译文 + 语法 JSON」（v1.0.5 之前的行为）
  新路径·首字       流式译文的第一个字（用户真正「感觉到」的延迟）
  新路径·译文完成   译文流结束
  新路径·语法      第二段语法讲解
"""
from __future__ import annotations

import argparse
import statistics
import tempfile
import time

from pathlib import Path

from galtl import llm
from galtl.config import Config
from galtl.ocr import make_ocr
from galtl import winapi
from galtl.pipeline import Engine
from galtl.store import Store
from galtl.textutil import display_join, normalize
from galtl.ocr import merge_lines

SAMPLE = "こんな時間まで起きているなんて、明日の朝が心配だよ……まったく。"


def ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


def pick_region(cfg: Config) -> tuple[int, int, int, int]:
    x, y, w, h = cfg.region
    if w >= 32 and h >= 16:
        return int(x), int(y), int(w), int(h)
    # 没框选过就退到屏幕中部一条，只为有东西可测
    vx, vy, vw, vh = winapi.virtual_screen()
    return int(vx + vw * 0.1), int(vy + vh * 0.7), int(vw * 0.8), int(vh * 0.15)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true", help="只测本地指纹与 OCR")
    args = ap.parse_args()

    cfg = Config()
    cfg.load()
    region = pick_region(cfg)
    print(f"区域 {region}   模型 {cfg.get('model')}   流式 {cfg.get('stream_translate')}")
    print("-" * 62)

    # 1) 指纹
    probe_ms = []
    for _ in range(5):
        t0 = time.perf_counter()
        winapi.grab_png(*region, scale=0.12, binarize=False)
        probe_ms.append(ms(t0))
    print(f"指纹 probe       {statistics.median(probe_ms):7.1f} ms  （×5 中位数）")

    # 2) OCR
    ocr = make_ocr(cfg.data)
    ocr.start()
    ocr_ms = []
    text = ""
    for _ in range(3):
        t0 = time.perf_counter()
        png, _, _ = winapi.grab_png(*region, scale=2.0, binarize=False)
        lines = ocr.recognize_png(png)
        text = normalize(display_join(merge_lines(lines)))
        ocr_ms.append(ms(t0))
    print(f"OCR 抓图+识别     {statistics.median(ocr_ms):7.1f} ms  （×3 中位数）")
    print(f"   识别结果：{text[:40] or '（这块区域没有日文，下面用样例台词代测）'}")

    if args.no_llm:
        ocr.close()
        return 0
    if not cfg.has_key:
        print("没配 api_key，跳过网络部分")
        ocr.close()
        return 0

    jp = text if any(ord(c) > 0x3000 for c in text) else SAMPLE
    kwargs = cfg.llm_kwargs()
    eng = Engine(cfg, Store(str(Path(tempfile.mkdtemp(prefix="lat_")) / "c.db")),
                 __import__("queue").Queue())

    # 3) 旧路径：一次调用出 JSON（译文 + 语法）
    print("-" * 62)
    t0 = time.perf_counter()
    old = llm.analyze_text(jp, kwargs)
    old_total = ms(t0)
    print(f"旧路径（一次 JSON） {old_total:7.1f} ms   译文 {old.get('zh', '')[:24]}…")

    # 4) 新路径：两段式
    t0 = time.perf_counter()
    first = None
    acc = []
    for piece in llm.translate_stream(jp, kwargs):
        if first is None:
            first = ms(t0)
        acc.append(piece)
    tr_done = ms(t0)
    zh = "".join(acc).strip()
    print(f"新路径·首字        {first or 0:7.1f} ms  ← 用户看到中文的时间")
    print(f"新路径·译文完成    {tr_done:7.1f} ms   全文 {zh[:24]}…")

    t0 = time.perf_counter()
    detail = llm.analyze_grammar(jp, zh, kwargs)
    gr_total = ms(t0)
    print(f"新路径·语法讲解    {gr_total:7.1f} ms   {len(detail.get('grammar') or [])} 条语法 / "
          f"{len(detail.get('vocab') or [])} 个词")

    print("-" * 62)
    shown = (probe_ms and statistics.median(probe_ms) or 0) + statistics.median(ocr_ms)
    print(f"总计：字幕出现 → 看见中文  ≈ {shown + (first or 0):7.0f} ms"
          f"（指纹 {statistics.median(probe_ms):.0f} + OCR {statistics.median(ocr_ms):.0f}"
          f" + 首字 {first or 0:.0f}）")
    print(f"      对比旧版          ≈ {shown + old_total:7.0f} ms"
          f"（旧版要等完整 JSON，首字就是全文）")
    ocr.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
