"""端到端自检（会弹一个小窗口并截屏，属于开发工具）。

    python e2e_check.py

验证链：Tk 模拟游戏对话框 → 屏幕截图 → WinRT 日语 OCR → 文本清洗 →
语法匹配 → 管线（无 Key 时走本地降级）→ 悬浮窗渲染。
"""
from __future__ import annotations

import json
import queue
import sys
import time
import tkinter as tk

# Windows 控制台默认 GBK，打印「〜」「｜」这类符号会抛 UnicodeEncodeError，中断自检
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

from galtl import grammar as gr
from galtl import winapi
from galtl.config import Config
from galtl.pipeline import Engine
from galtl.store import Store
from galtl.textutil import display_join, normalize
from galtl.ui_overlay import Overlay

LINES = [
    "こんな時間まで起きているなんて、",
    "明日の朝が心配だよ……まったく。",
]
OK, BAD = "[ ok ]", "[ !! ]"


def main() -> int:
    winapi.set_dpi_aware()
    cfg = Config()
    store = Store()
    events: "queue.Queue" = queue.Queue()

    root = tk.Tk()
    root.withdraw()

    # ---- 1. 模拟游戏对话框 ----
    box = tk.Toplevel(root)
    box.overrideredirect(True)
    box.attributes("-topmost", True)
    box.configure(bg="#12151b")
    inner = tk.Frame(box, bg="#1b1f27")
    inner.pack(fill="both", expand=True, padx=6, pady=6)
    for i, t in enumerate(LINES):
        tk.Label(inner, text=t, bg="#1b1f27", fg="#f4f6fb",
                 font=("Yu Gothic UI", 24), anchor="w").grid(
            row=i, column=0, sticky="w", padx=18, pady=(12 if i == 0 else 4, 0))
    box.geometry("1000x160+260+220")
    box.update()
    time.sleep(0.6)
    box.update()
    x, y = box.winfo_rootx(), box.winfo_rooty()
    w, h = box.winfo_width(), box.winfo_height()
    print(f"{OK} 模拟对话框 {w}×{h} @ ({x},{y})")
    cfg.set("region", [x, y, w, h])

    # ---- 2. 截图 ----
    t0 = time.perf_counter()
    png, pw, ph = winapi.grab_png(x, y, w, h, scale=float(cfg.get("upscale", 2.0)),
                                  binarize=bool(cfg.get("binarize", False)))
    print(f"{OK} 截图 {pw}×{ph} {(time.perf_counter()-t0)*1000:.0f}ms {len(png)}B")
    open("e2e_shot.png", "wb").write(png)

    # ---- 3. OCR ----
    engine = Engine(cfg, store, events)
    try:
        t0 = time.perf_counter()
        text, _, ms = engine.grab_text()
        print(f"{OK} OCR+清洗 {ms:.0f}ms  →  {text!r}")
        box.destroy()

        if not text:
            print(f"{BAD} OCR 没读到文字")
            return 1
        expect = normalize(display_join(LINES))
        same = sum(1 for a, b in zip(text, expect) if a == b)
        print(f"     与原文逐字一致 {same}/{len(expect)}  （OCR 有轻微误差属正常）")

        # ---- 4. 语法 ----
        hits = gr.match(text, limit=6)
        print(f"{OK} 语法命中 {len(hits)}：{[h['pattern'] for h in hits]}")
        for h in hits:
            print(f"       {h['pattern']}  {h.get('level','')}  {h.get('meaning','')}")

        # ---- 5. 管线（无 Key → 本地降级）----
        t0 = time.perf_counter()
        res = engine.translate(text, png)
        print(f"{OK} 管线 {(time.perf_counter()-t0)*1000:.0f}ms engine={(res.get('meta') or {}).get('engine')}")
        print(json.dumps(res, ensure_ascii=False, indent=2)[:900])

        # ---- 6. 悬浮窗 ----
        ov = Overlay(root, cfg, on_quit=lambda: None, on_settings=lambda: None,
                     on_lock=lambda _v: None)
        ov.show(res)
        root.update()
        time.sleep(0.8)
        root.update()
        print(f"{OK} 悬浮窗 hwnd={ov.win.winfo_id()} 可见={ov.win.winfo_viewable()}")
        time.sleep(1.2)
        ov.destroy()
    finally:
        try:
            box.destroy()
        except Exception:
            pass
        engine.close()
        store.close()
        root.destroy()

    print("\n端到端通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
