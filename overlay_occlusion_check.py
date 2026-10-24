"""验证「悬浮窗把自己译文挡进截图」的死循环，以及三种处理方式的差别。

造一个假游戏窗口（日语台词 + 闪烁 ▼，像真 galgame 那样持续动画），把真正的悬浮窗
压在对话框上（正如用户把字幕条放在屏幕底部、和对话框重叠那样），然后跑自动翻译：

  A off   不排除悬浮窗     → OCR 把上一句译文一起认回来，文本每帧都变，永远判不出稳定
  B mask  只把重叠处涂黑   → 不会读到自己了，但文字被挡掉一半，认不全
  C avoid 悬浮窗自动挪开   → 既不挡字也不进截图，两句都该认出来

用法：python overlay_occlusion_check.py
"""
from __future__ import annotations

import queue
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from galtl.config import Config
from galtl.pipeline import Engine
from galtl.store import Store
from galtl.ui_overlay import Overlay
from galtl.winapi import set_dpi_aware

LINES = [
    "こんな時間まで起きているなんて、明日の朝が心配だよ……まったく。",
    "ねえ、ちょっと聞いてる？　返事くらいしてよ。",
]
ZH = "都这个点还没睡，明天早上起不来我会担心的……真是的。"
MARK = ("都这个点", "都个点", "没睡", "明天", "担心")   # 悬浮窗自己中文译文的特征词（含 OCR 认歪的形）


def make_game(root: tk.Tk) -> tk.Toplevel:
    win = tk.Toplevel(root)
    win.title("假游戏")
    win.geometry("1100x300+200+700")
    win.attributes("-topmost", True)
    win.configure(bg="#101820")
    lbl = tk.Label(win, text=LINES[0], font=("Yu Gothic UI", 22), fg="#f2f7ff",
                   bg="#101820", justify="left", wraplength=1020, anchor="w")
    lbl.pack(fill="both", expand=True, padx=24, pady=18)
    arrow = tk.Label(win, text="▼", font=("Yu Gothic UI", 16), fg="#7fd4ff", bg="#101820")
    arrow.place(relx=1.0, x=-40, rely=1.0, y=-16, anchor="se")
    on = {"v": True}

    def blink() -> None:
        arrow.configure(text="▼" if on["v"] else "　")
        on["v"] = not on["v"]
        win.after(150, blink)

    win.after(150, blink)
    win.update()
    win.lbl = lbl  # type: ignore[attr-defined]
    return win


def run_case(root: tk.Tk, game: tk.Toplevel, mode: str, label: str) -> tuple[int, list[str]]:
    cfg = Config()
    cfg.load()
    gx, gy = game.winfo_rootx(), game.winfo_rooty()
    gw, gh = game.winfo_width(), game.winfo_height()

    cfg.set("mode", "ocr")
    cfg.set("region", [gx, gy, gw, gh])
    cfg.set("poll_ms", 200)
    cfg.set("stable_reads", 2)
    cfg.set("upscale", 2.0)
    cfg.set("binarize", False)
    cfg.set("min_ratio", 0.2)
    cfg.set("use_cache", False)
    # 悬浮窗故意压在对话框下沿（和用户的实际布局一样）
    cfg.set("overlay_pos", [gx + 60, gy + gh - 150])
    cfg.set("overlay_visible", True)

    ov = Overlay(root, cfg)
    ov.show({"jp": LINES[0], "zh": ZH, "grammar": [], "vocab": [], "notes": ""})
    ov.show_window()
    root.update()

    if mode == "avoid":
        moved = ov.avoid_rect(cfg.region)
        root.update()
        r = ov.screen_rect()
        print(f"    · 自动避让：{'挪了' if moved else '没挪'}；现位置 {r}")

    eng = Engine(cfg, Store(), queue.Queue(maxsize=256))
    eng.set_overlay_rect(ov.screen_rect if mode in ("mask", "avoid") else None)
    eng.preload()

    results = []
    t0 = time.time()
    t_end = t0 + 8.0
    switched = False
    while time.time() < t_end:
        if not switched and time.time() > t0 + 2.0:
            game.lbl.configure(text=LINES[1])      # 换一句台词
            root.update()                          # 让 Tk 真正重绘，否则屏幕没变
            switched = True
            print("    · 换台词")
        root.update()                              # 每轮驱动一次 Tk 事件循环
        try:
            res = eng.tick()
        except Exception as exc:                    # noqa: BLE001
            print(f"    tick 异常：{exc}")
            res = None
        seen = eng._seen
        if res:
            txt = (res.get("jp") or res.get("jp_ocr") or "").replace("\n", "⏎")
            results.append(txt)
            print(f"    ✅ {time.time()-t0:.1f}s 出结果 {txt[:26]!r}")
        elif seen:
            print(f"    · {time.time()-t0:.1f}s 等确认 hits={seen[3]}：{seen[1][:18]!r}")
        time.sleep(0.2)

    eng.close()
    ov.destroy()
    uniq = []
    for r in results:
        if r not in uniq:
            uniq.append(r)
    print(f"  [{label}] 出结果 {len(results)} 次 / {len(uniq)} 句")
    for r in uniq:
        print(f"      识别到：{r[:46]}")
    return len(uniq), uniq


def main() -> int:
    set_dpi_aware()
    root = tk.Tk()
    root.withdraw()
    game = make_game(root)
    root.update()

    print("A) 不排除悬浮窗（1.0.6 的行为）：")
    n_bad, texts_bad = run_case(root, game, mode="off", label="A off")
    game.lbl.configure(text=LINES[0])
    game.update()

    print("\nB) 只把悬浮窗涂黑：")
    n_mask, texts_mask = run_case(root, game, mode="mask", label="B mask")
    game.lbl.configure(text=LINES[0])
    game.update()

    print("\nC) 悬浮窗自动挪开（1.0.7 的行为）：")
    n_good, texts_good = run_case(root, game, mode="avoid", label="C avoid")

    root.destroy()
    polluted = any(m in t for t in texts_bad for m in MARK)
    clean_good = all(m not in t for t in texts_good for m in MARK)
    print("\n诊断：")
    print(f"  A 混进自己的译文：{'是' if polluted else '否'}（应「是」才说明复现了 bug）")
    print(f"  B 涂黑后能认出几句：{n_mask}（不保证，字被挡住一半）")
    print(f"  C 避让后认出几句：{n_good}（应为 2 句）")
    print(f"  C 结果不含自己的译文：{'是' if clean_good else '否'}")
    ok = n_good >= 2 and clean_good
    print("结论：" + ("自动避让后换台词能自己触发，且不读到自己" if ok else "仍有问题"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
