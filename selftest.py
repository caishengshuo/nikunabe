"""自检脚本：不打开 GUI，逐项验证 词典 / 截图 / OCR / 模型 四条链路。

    python selftest.py                   # 离线部分（截图 + OCR + 语法）
    python selftest.py --shot 10,20,600,120   # 用指定屏幕区域做 OCR
    python selftest.py --llm             # 额外测一次模型（消耗额度）
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from galtl import grammar as gr
from galtl import llm as llm_mod
from galtl import winapi
from galtl.config import Config
from galtl.ocr import make_ocr, merge_lines
from galtl.textutil import display_join, normalize

OK, BAD = "[ ok ]", "[ !! ]"
DEMO = "こんな時間まで起きているなんて、明日の朝が心配だよ"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--shot", default="")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    winapi.set_dpi_aware()
    vs = winapi.virtual_screen()
    print(f"Python {sys.version.split()[0]}  DPI {winapi.dpi_scale():.2f}  屏幕 {vs}")

    cfg = Config()
    print(f"配置 {cfg.path}")
    print(f"模型 {cfg.get('model')} @ {cfg.get('base_url')}  key={'有' if cfg.has_key else '无'}")

    # 1) 语法库
    print(f"{OK} 语法规则 {len(gr._RAW)} 条")
    hits = gr.match(DEMO, limit=5)
    print(f"{OK} 例句命中 {len(hits)}：{[h['pattern'] for h in hits]}")
    for h in hits[:3]:
        print(f"       {h['pattern']} {h.get('level','')} {h.get('meaning','')}")

    # 2) 截图 + OCR
    region = tuple(int(v) for v in args.shot.split(",")) if args.shot else cfg.region
    if len(region) != 4 or region[2] < 8 or region[3] < 8:
        print(f"{BAD} 没有可用区域，跳过 OCR（用 --shot x,y,w,h 指定）")
    else:
        t0 = time.perf_counter()
        png, w, h = winapi.grab_png(
            *region,
            scale=float(cfg.get("upscale", 2.0)),
            binarize=bool(cfg.get("binarize", False)),
        )
        print(f"{OK} 截图 {w}×{h} {(time.perf_counter()-t0)*1000:.0f}ms  {len(png)}B")
        ocr = make_ocr(cfg.data)
        try:
            t0 = time.perf_counter()
            ocr.start()
            info = getattr(ocr, "info", {}) or {}
            print(f"{OK} OCR 就绪 {info}  {(time.perf_counter()-t0)*1000:.0f}ms")
            t0 = time.perf_counter()
            lines = ocr.recognize_png(png)
            ms = (time.perf_counter() - t0) * 1000
            text = normalize(display_join(merge_lines(lines)))
            print(f"{OK} OCR {len(lines)} 行 {ms:.0f}ms")
            print("     原文：" + (text or "（空）"))
            if text:
                print("     语法：" + gr.as_hint(text, limit=3).replace("\n", " ／ "))
        except Exception as exc:
            print(f"{BAD} OCR 失败：{exc}")
        finally:
            ocr.close()

    # 3) 模型
    if args.llm or args.all:
        if not cfg.has_key and cfg.get("provider") != "ollama":
            print(f"{BAD} 没有 API Key，跳过模型测试")
        else:
            t0 = time.perf_counter()
            try:
                res = llm_mod.analyze_text(DEMO, cfg.llm_kwargs(),
                                           context=[], glossary=cfg.get("glossary") or "")
                print(f"{OK} 模型 {cfg.get('model')} {(time.perf_counter()-t0)*1000:.0f}ms")
                print(json.dumps(res, ensure_ascii=False, indent=2)[:1200])
            except Exception as exc:
                print(f"{BAD} 模型失败：{exc}")
                return 1

    print("\n检查完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
