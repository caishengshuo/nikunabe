"""覆盖原文（中文盖住日文）的端到端检查。

要证明三件事：
1. 覆盖层确实盖在识别区上（窗口矩形 == 框选矩形，并且可见）；
2. 覆盖层被「截图排除」——我们自己截图拿到的仍然是它背后的日文；
3. 因此自动翻译不受影响：盖着的情况下 grab_text 依然认得出日语原文。

任何一条不成立就说明功能是「看起来能用、实际把 OCR 弄瞎了」，必须报错。
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import time

sys.path.insert(0, r"G:\日语")

import tkinter as tk  # noqa: E402

from galtl import winapi  # noqa: E402
from galtl.config import Config  # noqa: E402
from galtl.pipeline import Engine  # noqa: E402
from galtl.store import Store  # noqa: E402
from galtl.ui_cover import CoverWindow  # noqa: E402

OK = "[OK]"
BAD = "[FAIL]"

RECT = (260, 660, 1100, 300)          # 假游戏对话框
JP = "こんな時間まで起きているなんて、明日の朝が心配だよ……まったく。"
ZH = "熬到这么晚还不睡，真担心你明天早上起得来吗……真是的。"


def mean_rgb(png: bytes) -> tuple[float, float, float]:
    from PIL import Image

    im = Image.open(io.BytesIO(png)).convert("RGB")
    px = im.tobytes()
    n = len(px) // 3
    return (sum(px[0::3]) / n, sum(px[1::3]) / n, sum(px[2::3]) / n)


def main() -> int:
    winapi.set_dpi_aware()
    fails: list[str] = []

    root = tk.Tk()
    root.geometry("1000x680+120+120")
    root.title("覆盖原文检查")

    # 假游戏：一块深色底 + 日文
    x, y, w, h = RECT
    game = tk.Toplevel(root)
    game.overrideredirect(True)
    game.geometry(f"{w}x{h}+{x}+{y}")
    game.configure(bg="#101722")
    lbl = tk.Label(game, text=JP, bg="#101722", fg="#f2f5fa",
                   font=("Yu Gothic UI", 24), wraplength=w - 60, justify="left")
    lbl.pack(expand=True, padx=30)
    game.attributes("-topmost", True)
    root.update()
    time.sleep(0.4)

    tmp = os.path.join(tempfile.gettempdir(), "galtl_cover_check")
    os.makedirs(tmp, exist_ok=True)
    cfg_path = os.path.join(tmp, "config.json")
    cfg = Config(cfg_path)
    cfg.set("region", [x, y, w, h])
    cfg.set("cover_mode", True)
    cfg.set("api_key", "")
    cfg.set("grammar_phase", False)
    cfg.save()

    store = Store(os.path.join(tmp, "cache.db"))
    events = type("Q", (), {"put_nowait": lambda self, item: None, "get": lambda self, **k: (_ for _ in ()).throw(__import__("queue").Empty), "get_nowait": lambda self: (_ for _ in ()).throw(__import__("queue").Empty)})()
    engine = Engine(cfg, store, events)

    cover = CoverWindow(root, cfg)
    engine.cover = cover
    cover.set_rect((x, y, w, h))
    root.update()

    # --- 1. 定位 ---
    cover.show(ZH)
    root.update()
    time.sleep(0.35)
    if not cover.visible:
        fails.append("覆盖层没显示")
    rect_now = winapi.window_rect(cover._hwnd)
    want = (x, y, x + w, y + h)
    if rect_now and tuple(rect_now) != want:
        fails.append(f"覆盖层位置不对：{rect_now} 应为 {want}（window_rect 返回 x1,y1,x2,y2）")
    print(f"{OK} 覆盖层 rect={rect_now} excluded={cover.excluded}")

    # --- 2. 截图排除 ---
    png, pw, ph = winapi.grab_png(x, y, w, h, scale=1.0, binarize=False)
    r_dark = mean_rgb(png)
    cover.hide()
    root.update()
    time.sleep(0.3)
    png2, _, _ = winapi.grab_png(x, y, w, h, scale=1.0, binarize=False)
    r_plain = mean_rgb(png2)
    diff = sum(abs(a - b) for a, b in zip(r_dark, r_plain))
    print(f"     盖住时平均色={tuple(round(v, 1) for v in r_dark)} 关掉时={tuple(round(v, 1) for v in r_plain)} 差={diff:.1f}")
    if not cover.excluded:
        fails.append("本机不支持截图排除（SetWindowDisplayAffinity 失败），只能走闪烁取样退化路径")
    elif diff > 6:
        fails.append(f"覆盖层被截进了截图（平均色差 {diff:.1f}），OCR 会读到自己写的中文")

    # --- 3. 盖着也能认出日文 ---
    cover.show(ZH)
    root.update()
    time.sleep(0.3)
    try:
        text, shot, ms = engine.grab_text()
    except Exception as exc:
        fails.append(f"grab_text 异常：{exc}")
        text, ms = "", 0
    jp_hit = any(k in text for k in ("起きて", "時間", "明日", "心配", "なんて", "こんな"))
    zh_leak = any(k in text for k in ("熬到", "担心", "睡", "真是的"))
    print(f"     OCR({ms:.0f}ms) 文本={text[:60]!r}")
    if not jp_hit:
        fails.append(f"盖住后 OCR 认不出日文：{text!r}")
    if zh_leak:
        fails.append(f"OCR 读到了覆盖层的中文（说明没被排除）：{text!r}")

    cover.destroy()
    try:
        engine.close()
    except Exception:
        pass
    root.destroy()

    if fails:
        for f in fails:
            print(f"{BAD} {f}")
        return 1
    print(f"{OK} 覆盖原文可用：中文盖住日文，日文仍可被读取")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
