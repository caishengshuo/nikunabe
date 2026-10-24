"""框选字幕区域：冻结整屏截图 + 拖拽矩形，返回绝对屏幕坐标。

用冻结帧而不是半透明覆盖层，框选时画面静止，能精确对齐文本框边缘；
也避免多显示器负坐标在 Tk geometry 里的解析问题（改用 SetWindowPos 直接摆位）。
"""
from __future__ import annotations

import tkinter as tk

from . import winapi


class RegionSelector:
    def __init__(self, master: tk.Tk):
        self.result: tuple[int, int, int, int] | None = None
        self.win = tk.Toplevel(master)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.canvas = tk.Canvas(self.win, bd=0, highlightthickness=0,
                                cursor="crosshair", bg="#101216")
        self.canvas.pack(fill="both", expand=True)
        self._start = None
        self._rect = None
        self._photo = None
        self._vx = self._vy = 0
        self._scale = 1.0
        self._photo_w = self._photo_h = 0

        self.canvas.bind("<Button-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.win.bind("<Escape>", lambda _e: self._cancel())
        self.canvas.bind("<Button-3>", lambda _e: self._cancel())
        self.win.bind("<Return>", lambda _e: self._release(None))

    def select(self) -> tuple[int, int, int, int] | None:
        vx, vy, vw, vh = winapi.virtual_screen()
        self._vx, self._vy = vx, vy
        png, pw, ph = winapi.grab_png(vx, vy, vw, vh, scale=1.0, binarize=False)
        self._photo = tk.PhotoImage(data=png, format="png")
        self._photo_w, self._photo_h = pw, ph

        self.win.deiconify()
        self.win.update_idletasks()
        try:
            winapi.place_window(self.win.winfo_id(), vx, vy, vw, vh, topmost=True)
        except Exception:
            self.win.geometry(f"{vw}x{vh}+0+0")
        self.canvas.configure(width=vw, height=vh)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw")
        hint = (
            "拖拽框选游戏文本框　·　回车确认　·　Esc/右键取消\n"
            f"虚拟桌面 {vx},{vy} {vw}×{vh}"
        )
        self.canvas.create_text(
            vw // 2, 40, text=hint, fill="#ffd479",
            font=("Microsoft YaHei UI", 16), justify="center",
        )
        self.win.focus_force()
        self.win.wait_window()
        return self.result

    def _press(self, ev):
        self._start = (ev.x, ev.y)
        if self._rect:
            self.canvas.delete(self._rect)
        self._rect = self.canvas.create_rectangle(
            ev.x, ev.y, ev.x, ev.y, outline="#39d98a", width=2, dash=(6, 4)
        )

    def _drag(self, ev):
        if not self._start or not self._rect:
            return
        x0, y0 = self._start
        self.canvas.coords(self._rect, x0, y0, ev.x, ev.y)
        self.canvas.delete("size")
        w, h = abs(ev.x - x0), abs(ev.y - y0)
        self.canvas.create_text(
            min(x0, ev.x) + 6, max(6, min(y0, ev.y) - 14),
            text=f"{w}×{h}", fill="#39d98a", anchor="w",
            font=("Consolas", 12), tags="size",
        )

    def _release(self, ev):
        if not self._start:
            self._cancel()
            return
        if ev is not None:
            x1, y1 = ev.x, ev.y
        else:
            coords = self.canvas.coords(self._rect) if self._rect else None
            if not coords:
                self._cancel()
                return
            x1, y1 = coords[2], coords[3]
        x0, y0 = self._start
        left, top = min(x0, x1), min(y0, y1)
        w, h = abs(x1 - x0), abs(y1 - y0)
        if w < 8 or h < 8:
            self._cancel()
            return
        self.result = (int(self._vx + left), int(self._vy + top), int(w), int(h))
        self._close()

    def _cancel(self):
        self.result = None
        self._close()

    def _close(self):
        try:
            self.win.withdraw()
            self.win.destroy()
        except Exception:
            pass
