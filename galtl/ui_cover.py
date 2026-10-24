"""覆盖层：把中文译文直接盖在游戏原文的位置上，把原日文遮住。

和「底部字幕条」（ui_overlay.py）的分工：
- 字幕条：不挡游戏，日文/中文/语法/生词一起看，适合学习。
- 覆盖层：只管一件事——让游戏看起来像官方中文版，适合纯享受剧情。

零闪烁的关键是 ``SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)``：
这个窗口对「屏幕捕获」不可见，于是玩家屏幕上是中文，而本程序自己截屏拿到的
仍是它背后的原日文。所以自动翻译的判重、换句检测完全不受影响。
系统不支持时（老 Windows），退化为「取样瞬间把自己藏起来」（Engine 负责调用 dip）。
"""
from __future__ import annotations

import logging
import tkinter as tk
from tkinter import font as tkfont

from . import winapi

log = logging.getLogger("galtl.cover")


def _median_ring_color(rect: tuple[int, int, int, int]) -> str:
    """抓一次识别区，取四周一圈像素的中位色——那就是游戏对话框的底色。"""
    try:
        from . import winapi

        buf, w, h = winapi.capture_bgra(*rect)
        step = max(1, min(w, h) // 60)
        px = []
        for x in range(0, w, step):                      # 上下两条边
            for y in (0, 1, h - 2, h - 1):
                i = (y * w + x) * 4
                px.append((buf[i + 2], buf[i + 1], buf[i]))
        for y in range(0, h, step):                      # 左右两条边
            for x in (0, 1, w - 2, w - 1):
                i = (y * w + x) * 4
                px.append((buf[i + 2], buf[i + 1], buf[i]))
        if not px:
            return ""
        px.sort()
        r, g, b = px[len(px) // 2]
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return ""


class CoverWindow:
    """一块贴在识别区上的中文遮罩。"""

    def __init__(self, root: tk.Tk, cfg) -> None:
        self.cfg = cfg
        self.root = root
        self._text = ""
        self._rect: tuple[int, int, int, int] | None = None
        self._bg_color: str | None = None      # 从游戏对话框取样来的底色
        self._visible = False
        self._dipped = False
        self._enabled = bool(cfg.get("cover_mode", False))
        self.excluded = False

        bg = str(cfg.get("cover_bg", "") or "#0b0f16")
        fg = str(cfg.get("cover_fg", "") or "#ffffff")
        alpha = float(cfg.get("cover_alpha", 1.0) or 1.0)

        win = self.win = tk.Toplevel(root)
        win.withdraw()
        win.overrideredirect(True)
        win.configure(bg=bg)
        try:
            win.attributes("-topmost", True)
            win.attributes("-alpha", max(0.2, min(1.0, alpha)))
        except tk.TclError:
            pass

        self._family = str(cfg.get("font_zh", "") or "Microsoft YaHei UI")
        self.txt = tk.Text(
            win, bg=bg, fg=fg, bd=0, highlightthickness=0, wrap="word",
            cursor="arrow", padx=10, pady=8, takefocus=0,
            width=1, height=1,
            insertwidth=0, spacing1=0, spacing2=2, spacing3=0,
            font=(self._family, 20),
        )
        self.txt.pack(fill="both", expand=True)
        self.txt.tag_configure("body", justify="center", spacing1=0, spacing2=3)
        self.txt.configure(state="disabled")

        win.update_idletasks()
        self._hwnd = winapi.top_hwnd(win)
        # 点击穿透：盖住的地方照样能点游戏（存档、选项、快进）
        winapi.set_click_through(self._hwnd, True)
        if bool(cfg.get("cover_exclude_capture", True)):
            self.excluded = winapi.set_capture_excluded(self._hwnd, True)

    # ---------- 状态 ----------
    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def visible(self) -> bool:
        return self._visible

    def set_rect(self, rect) -> None:
        """识别区变化（框选、改变）时跟上；窗口正在显示就立刻挪过去。"""
        try:
            r = tuple(int(v) for v in rect) if rect else None
        except Exception:
            r = None
        if r and len(r) == 4:
            x, y, w, h = r
            r = (x, y, w, h) if w > 8 and h > 8 else None
        else:
            r = None
        if r != self._rect:
            self._bg_color = None              # 换了区域就重新取样
        self._rect = r
        if r and self._visible and not self._dipped:
            self._place()

    def apply_theme(self) -> None:
        """设置页改完颜色/透明度后调用。"""
        bg = str(self.cfg.get("cover_bg", "") or "#0b0f16")
        fg = str(self.cfg.get("cover_fg", "") or "#ffffff")
        alpha = float(self.cfg.get("cover_alpha", 1.0) or 1.0)
        self._family = str(self.cfg.get("font_zh", "") or "Microsoft YaHei UI")
        self.win.configure(bg=bg)
        self.txt.configure(bg=bg, fg=fg)
        try:
            self.win.attributes("-alpha", max(0.2, min(1.0, alpha)))
        except tk.TclError:
            pass
        if self._visible:
            self._render(self._text)

    # ---------- 显示 ----------
    def show(self, zh: str) -> None:
        zh = (zh or "").strip()
        if not self._enabled or not self._rect or not zh:
            self.hide()
            return
        if zh == self._text and self._visible and not self._dipped:
            return
        self._text = zh
        self._render(zh)
        self._place()
        if not self._dipped:
            self.win.deiconify()
            self.win.lift()
            self._visible = True

    def hide(self) -> None:
        if self._visible:
            self.win.withdraw()
            self._visible = False

    def toggle(self) -> bool:
        self._enabled = not self._enabled
        self.cfg.data["cover_mode"] = self._enabled
        try:
            self.cfg.save()
        except Exception:
            pass
        if self._enabled:
            if self._text:
                self.show(self._text)
        else:
            self.hide()
        return self._enabled

    # ---------- 系统不支持排除时：取样瞬间隐身 ----------
    def dip(self, on: bool) -> None:
        if self.excluded or not self._visible:
            return
        if on and not self._dipped:
            self.win.withdraw()
            self._dipped = True
            try:
                self.win.update()
            except tk.TclError:
                pass
        elif not on and self._dipped:
            self._dipped = False
            self._place()
            self.win.deiconify()
            try:
                self.win.update()
            except tk.TclError:
                pass

    # ---------- 内部 ----------
    def _sample_bg(self) -> None:
        """把游戏对话框的底色采样过来——这样盖上去的中文块不会有「色差补丁」感。"""
        if not self._rect or not bool(self.cfg.get("cover_match_bg", True)):
            return
        if self._visible and not self.excluded:
            return          # 自己没被排除在截图外时，取样会拍到自己的中文
        col = _median_ring_color(self._rect)
        if not col:
            return
        self._bg_color = col
        try:
            self.win.configure(bg=col)
            self.txt.configure(bg=col)
        except tk.TclError:
            pass

    def _place(self) -> None:
        if not self._rect:
            return
        self._sample_bg()
        x, y, w, h = self._rect
        # 尺寸交给 Tk（geometry），位置+置顶交给 Win32：
        # 只调 SetWindowPos 的话 Tk 下一轮 idle 会按 Text 的请求尺寸把窗口撑大。
        try:
            self.win.geometry(f"{w}x{h}+{x}+{y}")
            self.win.update_idletasks()
        except tk.TclError:
            return
        winapi.place_window(self._hwnd, x, y, w, h, topmost=True)

    def _render(self, zh: str) -> None:
        x, y, w, h = self._rect or (0, 0, 800, 120)
        pad_x = 12
        pad_y = 10
        size = self._fit_size(zh, w - 2 * pad_x, h - 2 * pad_y)
        self.txt.configure(
            state="normal", font=(self._family, size),
            padx=pad_x, pady=self._pad_y_for(zh, size, w - 2 * pad_x, h - 2 * pad_y),
        )
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", zh, "body")
        self.txt.configure(state="disabled")
        log.debug("cover render %r size=%d rect=%s", zh[:40], size, self._rect)

    def _fit_size(self, text: str, width: int, height: int) -> int:
        hi = int(self.cfg.get("cover_max_size", 30) or 30)
        lo = int(self.cfg.get("cover_min_size", 13) or 13)
        hi = max(lo, min(hi, 96))
        for size in range(hi, lo - 1, -1):
            f = tkfont.Font(family=self._family, size=size)
            lines = self._wrapped_lines(text, f, max(40, width))
            line_h = f.metrics("linespace") + 3
            if lines * line_h <= max(24, height):
                return size
        return lo

    def _pad_y_for(self, text: str, size: int, width: int, height: int) -> int:
        f = tkfont.Font(family=self._family, size=size)
        lines = self._wrapped_lines(text, f, max(40, width))
        used = lines * (f.metrics("linespace") + 3)
        return max(6, int((max(0, height - used) / 2)) + 6)

    @staticmethod
    def _wrapped_lines(text: str, font: tkfont.Font, width: int) -> int:
        """按像素宽度做贪心换行，返回行数（只用来量高度，不真的排版）。"""
        lines = 1
        cur = 0
        for ch in text:
            if ch == "\n":
                lines += 1
                cur = 0
                continue
            cw = font.measure(ch)
            if cur + cw > width and cur > 0:
                lines += 1
                cur = cw
            else:
                cur += cw
        return lines

    def destroy(self) -> None:
        try:
            self.win.destroy()
        except tk.TclError:
            pass
