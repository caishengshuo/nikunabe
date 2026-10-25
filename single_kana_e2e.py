# -*- coding: utf-8 -*-
"""真机端到端：屏幕上只有「ん」一个假名时，程序能不能读出来、翻出去。

用的是真 Windows OCR + 真模型（读 dist\\config.json 里的 key），
不是 mock。跑完会打印每一帧的状态。
"""

from __future__ import annotations

import io
import queue
import sys
import time
import tkinter as tk

sys.path.insert(0, r"G:\日語".replace("語", "语"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import ctypes

ctypes.windll.shcore.SetProcessDpiAwareness(2)   # 必须在 tk.Tk() 之前

from galtl.config import Config
from galtl.pipeline import Engine
from galtl.store import Store


def main() -> int:
    cfg = Config(r"G:\日语\dist\config.json")     # 用用户那份配置（里面有真 key）
    cfg.load()
    cfg.data.update({
        "mode": "ocr",
        "region": [240, 520, 900, 260],
        "poll_ms": 120,
        "stable_reads": 2,
        "settle_ms": 120,
        "upscale": 2.0,
        "auto_translate": True,
        "glyph_fallback": True,
        "punct_fallback": True,
    })
    store = Store()
    events: "queue.Queue" = queue.Queue()
    eng = Engine(cfg, store, events)

    root = tk.Tk()
    root.title("single-kana-e2e")
    root.overrideredirect(True)
    root.geometry("900x260+240+520")
    root.configure(bg="#12161a")
    root.attributes("-topmost", True)
    lbl = tk.Label(root, text="ん", font=("Yu Gothic UI", 56), fg="#ecf0f6", bg="#12161a")
    lbl.place(relx=0.06, rely=0.5, anchor="w")
    root.update()

    print("窗口已就位，开始 tick（真 OCR + 真模型）…")
    t0 = time.time()
    got = None
    frames = 0
    while time.time() - t0 < 25:
        root.update()
        frames += 1
        r = eng.tick()
        while not events.empty():
            kind, data = events.get_nowait()
            if kind == "status":
                print(f"    status: {data}")
        if r:
            got = r
            break
        time.sleep(0.12)

    if got:
        print(f"\n识别到 jp={got.get('jp')!r}  kana={got.get('kana')!r}")
        print(f"译文 zh={got.get('zh')!r}")
        print(f"语法 {len(got.get('grammar') or [])} 条 / 生词 {len(got.get('vocab') or [])} 个")
        print(f"meta={got.get('meta')}")
    else:
        print(f"25 秒内没有结果（跑了 {frames} 帧）")
    root.destroy()
    return 0 if got else 1


if __name__ == "__main__":
    raise SystemExit(main())
