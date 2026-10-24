"""界面预览：渲染一份真实结果到悬浮窗与主窗口，并各截一张图。

    python ui_preview.py        # 生成 preview_overlay.png / preview_main.png
"""
from __future__ import annotations

import queue
import time
import tkinter as tk

from galtl import winapi
from galtl.config import Config
from galtl.pipeline import Engine
from galtl.store import Store

RESULT = {
    "noise": False,
    "jp": "こんな時間まで起きているなんて、明日の朝が心配だよ……まったく。",
    "kana": "こんなじかんまでおきているなんて、あしたのあさがしんぱいだよ……まったく",
    "zh": "居然熬到这么晚，明早可真让人担心啊……真是的。",
    "literal": "竟然一直醒着到这种时间，明天的早上很让人担心哦……真是的。",
    "grammar": [
        {"pattern": "〜まで", "level": "N5", "meaning": "到…为止／甚至连…",
         "explain": "表示时间或范围的终点，这里是「熬到这种时间」。", "form": "時間まで"},
        {"pattern": "〜なんて", "level": "N3", "meaning": "竟然…／…什么的",
         "explain": "提示意外与感叹，带责怪又关心的语气。", "form": "起きているなんて"},
        {"pattern": "〜ている", "level": "N5", "meaning": "正在…／持续状态",
         "explain": "動作の継続。「起きている」＝醒着、没睡。", "form": "起きている"},
    ],
    "vocab": [
        {"word": "心配", "reading": "しんぱい", "meaning": "担心"},
        {"word": "まったく", "reading": "まったく", "meaning": "真是的（埋怨）"},
    ],
    "notes": "语气是半埋怨半关心，带独占欲；翻译保留「啊／真是的」的亲近感。",
    "meta": {"ms": 1180, "engine": "text:deepseek-chat"},
}


def shot(widget, path: str) -> None:
    widget.update_idletasks()
    time.sleep(0.35)
    widget.update()
    x, y = widget.winfo_rootx(), widget.winfo_rooty()
    w, h = widget.winfo_width(), widget.winfo_height()
    png, pw, ph = winapi.grab_png(x, y, w, h, scale=1.0, binarize=False)
    with open(path, "wb") as f:
        f.write(png)
    print(f"[ ok ] {path} {pw}×{ph}")


def shot_hwnd(widget, path: str) -> None:
    """按窗口句柄抓图：窗口不在最前也能拍到（PrintWindow）。"""
    widget.update_idletasks()
    widget.update()
    time.sleep(0.35)
    hwnd = winapi.hwnd_of(widget)
    png, pw, ph = winapi.grab_hwnd_png(int(hwnd), scale=1.0, binarize=False)
    with open(path, "wb") as f:
        f.write(png)
    print(f"[ ok ] {path} {pw}×{ph} (hwnd)")


def main() -> None:
    winapi.set_dpi_aware()
    cfg = Config()
    store = Store()
    events: "queue.Queue" = queue.Queue()
    engine = Engine(cfg, store, events)

    root = tk.Tk()
    root.withdraw()

    # ---- 悬浮窗 ----
    from galtl.ui_overlay import Overlay

    ov = Overlay(root, cfg, on_quit=lambda: None, on_settings=lambda: None,
                 on_lock=lambda _v: None)
    ov.show(RESULT)
    ov.show_window()
    root.update()
    time.sleep(0.5)
    shot(ov.win, "preview_overlay.png")
    ov.hide_window()

    # ---- 主窗口 ----
    from galtl.ui_main import MainWindow

    cfg.set("overlay_visible", False)
    app = MainWindow(root, cfg, store, engine, events)
    app.root.geometry("780x700+120+80")
    app.root.deiconify()
    app.root.update()
    app._render_preview(RESULT)
    app.set_status("缓存 1180ms ｜ OCR 12 · 模型 9 · 缓存命中 3 · 失败 0")
    root.update()
    time.sleep(0.4)
    shot_hwnd(app.root, "preview_main.png")
    app.root.withdraw()

    engine.close()
    store.close()
    root.destroy()


if __name__ == "__main__":
    main()
