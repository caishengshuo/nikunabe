"""假 galgame 对话框：日语台词 + 闪烁 ▼ + 立绘呼吸效果，每 4 秒换一句。

用来做「真 exe」的端到端验证：谁都别按 F9，看它能不能自己翻。
"""
from __future__ import annotations

import tkinter as tk

from galtl.winapi import set_dpi_aware

LINES = [
    "こんな時間まで起きているなんて、明日の朝が心配だよ……まったく。",
    "ねえ、ちょっと聞いてる？　返事くらいしてよ。",
    "べ、別にアンタのことなんか待ってないんだからねっ！",
]


def main() -> None:
    set_dpi_aware()      # 不声明的话 Tk 的坐标是逻辑像素，截图/OCR 走物理像素，两边对不上
    root = tk.Tk()
    root.title("假游戏 · 自动翻译验证")
    root.geometry("1100x300+200+620")
    root.configure(bg="#101820")
    root.attributes("-topmost", True)
    lbl = tk.Label(root, text=LINES[0], font=("Yu Gothic UI", 22), fg="#f2f7ff",
                   bg="#101820", justify="left", wraplength=1020, anchor="w")
    lbl.pack(fill="both", expand=True, padx=24, pady=18)
    arrow = tk.Label(root, text="▼", font=("Yu Gothic UI", 16), fg="#7fd4ff", bg="#101820")
    arrow.place(relx=1.0, x=-40, rely=1.0, y=-16, anchor="se")
    state = {"i": 0, "on": True}

    def blink() -> None:
        arrow.configure(text="▼" if state["on"] else "　")
        state["on"] = not state["on"]
        root.after(150, blink)

    def next_line() -> None:
        state["i"] = (state["i"] + 1) % len(LINES)
        lbl.configure(text=LINES[state["i"]])
        root.after(4000, next_line)

    root.after(150, blink)
    root.after(4000, next_line)
    root.mainloop()


if __name__ == "__main__":
    main()
