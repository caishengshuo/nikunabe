"""端到端测「台词出现 → 译文 → 语法/生词」的可见时间轴。

假游戏窗口里跑真实 OCR + DeepSeek 接口，记录：
  ① 第一帧带译文的结果（zh 非空，由 tick() 直接返回，界面立刻渲染）
  ② 同帧就带上的离线语法/生词（本地句型库 + 词表，0 毫秒级）
  ③ 模型流式补上的第一条语法
  ④ 模型讲完
  ⑤ 换句后不按 F9 也能自己出结果

用法：C:\\Python314\\python.exe enrich_e2e.py
"""
from __future__ import annotations

import queue
import sys
import time
import tkinter as tk
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from galtl.config import Config
from galtl.pipeline import Engine
from galtl.store import Store
from galtl.winapi import set_dpi_aware

LINES = [
    "べ、別にアンタのことなんか待ってないんだからねっ！",
    "こんな時間まで起きているなんて、明日の朝が心配だよ……まったく。",
]


def main() -> int:
    set_dpi_aware()
    root = tk.Tk()
    root.title("假游戏·讲解延迟验证")
    root.geometry("1100x300+200+620")
    root.attributes("-topmost", True)
    root.configure(bg="#101820")
    lbl = tk.Label(root, text=LINES[0], font=("Yu Gothic UI", 26), fg="#f2f7ff",
                   bg="#101820", justify="left", wraplength=1040, anchor="w")
    lbl.pack(fill="both", expand=True, padx=20, pady=20)
    for _ in range(3):
        root.update()
        time.sleep(0.15)

    cfg = Config()
    cfg.load()
    rect = [root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height()]
    cfg.data.update({
        "region": rect,
        "upscale": 2.0,
        "poll_ms": 200,
        "auto_translate": True,
        "use_cache": False,          # 强制真实请求，不被缓存骗
        "grammar_async": True,
        "ocr_pixel_budget": 3000000,
        "stable_reads": 2,
        "use_probe": False,
    })

    events: "queue.Queue" = queue.Queue()
    store = Store(Path("_e2e_enrich.db"))
    eng = Engine(cfg, store, events)
    eng.preload()

    state = {"t0": time.perf_counter(), "switched": False, "sw_time": 0.0}
    mark: dict[str, float] = {}

    def el() -> float:
        return (time.perf_counter() - state["t0"]) * 1000

    def handle(data: dict, where: str) -> None:
        jp = (data.get("jp") or "").strip()
        zh = (data.get("zh") or "").strip()
        gram = data.get("grammar") or []
        vocab = data.get("vocab") or []
        enrich = bool(data.get("enrich"))
        streaming = bool(data.get("streaming"))
        tag = "流式补讲" if streaming else ("讲解完成" if enrich else "首帧")
        print(f"  {el():7.0f}ms  [{where}/{tag}] {jp[:14]} zh={zh[:12]!r} 语法{len(gram)} 生词{len(vocab)}")

        if not state["switched"] and jp.startswith(LINES[0][:5]):
            if zh:
                mark.setdefault("zh", el())
            if (gram or vocab) and not enrich:
                mark.setdefault("offline", el())
            if enrich and streaming and gram:
                mark.setdefault("model_first", el())
            if enrich and not streaming and gram:
                mark.setdefault("model_done", el())
            if "model_done" in mark:
                lbl.configure(text=LINES[1])
                root.update()
                state["switched"] = True
                state["sw_time"] = time.perf_counter()
                print("  ------- 换成第二句（不按 F9）-------")
        elif state["switched"] and jp.startswith(LINES[1][:5]):
            if zh and (gram or vocab):
                mark["second"] = (time.perf_counter() - state["sw_time"]) * 1000
                print(f"  ------- 第二句自动出结果：换句后 {mark['second']:.0f}ms -------")
                state["done"] = True

    deadline = time.time() + 45
    last_tick = 0.0
    while time.time() < deadline and not state.get("done"):
        root.update()
        if time.time() - last_tick >= 0.2:
            last_tick = time.time()
            try:
                r = eng.tick()
            except Exception as exc:
                print(f"  tick 异常: {exc}")
                r = None
            if r:
                handle(r, "直返")
        try:
            while True:
                kind, data = events.get_nowait()
                if kind == "result":
                    handle(data, "事件")
        except queue.Empty:
            pass

    eng.close()
    store.close()
    root.destroy()

    print()
    print("=== 时间轴（从台词出现算起）===")
    for k, label in (("zh", "看到中文译文"), ("offline", "同帧看到语法/生词（离线）"),
                     ("model_first", "模型第一条语法（流式）"), ("model_done", "模型讲完"),
                     ("second", "换句后自动触发")):
        if k in mark:
            print(f"  {label}: {mark[k]:.0f}ms")
    ok = (all(k in mark for k in ("zh", "offline", "model_first"))
          and "second" in mark
          and mark["offline"] - mark["zh"] < 50
          and mark["model_first"] < mark["model_done"])
    print("  OK：译文与离线语法/生词同帧出现，模型讲解随后流式补上，换句自动触发" if ok
          else "  失败：" + str(sorted(mark)))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
