"""悬浮字幕窗：置顶、无边框、可拖动/缩放、可鼠标穿透。

内容三块：日文原句 / 中文翻译 / 语法解析（+词表、语气提示）。
用 Text 控件承载（不同字号与颜色标签），窗口高度按 displaylines 自适配。
"""
from __future__ import annotations

import logging
import tkinter as tk
from tkinter import font as tkfont

from . import winapi

log = logging.getLogger("galtl.overlay")

THEMES = {
    "dark": {
        "bg": "#0e1014",
        "border": "#2a3140",
        "jp": "#9fb0c6",
        "kana": "#6d7c92",
        "zh": "#f2f5fa",
        "gram_head": "#7fd6c1",
        "gram": "#c8d4e2",
        "note": "#e8c27a",
        "meta": "#5a6675",
        "glow": "#0e1014",
    },
    "light": {
        "bg": "#fbfbfd",
        "border": "#c8cdd8",
        "jp": "#5b6675",
        "kana": "#8a94a4",
        "zh": "#14171c",
        "gram_head": "#0f7a6a",
        "gram": "#2c3440",
        "note": "#9a6a12",
        "meta": "#98a1b0",
        "glow": "#fbfbfd",
    },
}


class Overlay:
    def __init__(self, master: tk.Tk, cfg, on_quit=None, on_settings=None, on_lock=None):
        self.cfg = cfg
        self.on_quit = on_quit
        self.on_settings = on_settings
        self.on_lock = on_lock
        self.win = tk.Toplevel(master)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            from . import uiicon

            uiicon.apply(self.win)
        except Exception:
            pass
        self.win.configure(bg=THEMES["dark"]["border"])
        self._hwnd = None
        self._drag = None
        # 位置以这两个字段为准：Win32 搬窗口后 Tk 的 winfo_x/y 不会跟着更新，
        # 每次 _fit_height 又按 cfg 重算位置的话，用户拖动就会被下一次渲染拉回原地。
        self._cur_pos: list[int] | None = None
        self._auto_pos: bool | None = None
        self._current: dict = {}
        self._build()
        self.apply_config()
        if cfg.get("overlay_visible", True):
            self.show_window()

    # ---------- 构建 ----------
    def _build(self) -> None:
        self.body = tk.Frame(self.win, bd=0, highlightthickness=0)
        self.body.pack(fill="both", expand=True, padx=1, pady=1)

        hdr = tk.Frame(self.body, height=18)
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)
        self.hdr = hdr
        self.grip = tk.Label(hdr, text="┅", bd=0, cursor="fleur", font=("Segoe UI", 9))
        self.grip.pack(side="left", padx=(8, 0))
        self.hint = tk.Label(hdr, text="", bd=0, font=("Segoe UI", 8), cursor="fleur")
        self.hint.pack(side="left", padx=(8, 0))
        self.meta = tk.Label(hdr, text="", bd=0, font=("Consolas", 8))
        self.meta.pack(side="right", padx=8)

        self.text = tk.Text(
            self.body, bd=0, highlightthickness=0, wrap="word", width=1,
            padx=14, pady=4, height=3, spacing1=0, spacing3=2,
            insertwidth=0, cursor="fleur",
        )
        self.text.pack(fill="both", expand=True, padx=0, pady=(0, 8))
        self.text.configure(state="disabled")

        for widget in (hdr, self.grip, self.hint, self.meta, self.text, self.body):
            widget.bind("<Button-1>", self._on_press)
            widget.bind("<B1-Motion>", self._on_drag)
            widget.bind("<ButtonRelease-1>", self._on_release)
            widget.bind("<Button-3>", self._on_menu)
            widget.bind("<MouseWheel>", self._on_wheel)

        self.menu = tk.Menu(self.win, tearoff=0)
        self.menu.add_command(label="复制日语原句", command=self._copy_jp)
        self.menu.add_command(label="复制中文翻译", command=self._copy_zh)
        self.menu.add_separator()
        self.menu.add_command(label="字号 +", command=lambda: self._bump_font(+1))
        self.menu.add_command(label="字号 −", command=lambda: self._bump_font(-1))
        self.menu.add_separator()
        self.menu.add_command(label="位置重置（底部居中）", command=self.reset_pos)
        self.menu.add_separator()
        self.menu.add_command(label="锁定 / 鼠标穿透（F6）", command=self._toggle_lock)
        self.menu.add_command(label="设置…", command=lambda: self.on_settings and self.on_settings())
        self.menu.add_command(label="隐藏字幕（F10）", command=self.hide_window)

    # ---------- 配置 ----------
    def apply_config(self) -> None:
        theme = THEMES.get(self.cfg.get("theme", "dark"), THEMES["dark"])
        self.theme = theme
        size = int(self.cfg.get("zh_size", 19))
        jp = int(self.cfg.get("jp_size", 12))
        gs = int(self.cfg.get("gram_size", 11))
        fj = self.cfg.get("font_jp", "Yu Gothic UI")
        fz = self.cfg.get("font_zh", "Microsoft YaHei UI")

        self.f_jp = tkfont.Font(family=fj, size=jp)
        self.f_kana = tkfont.Font(family=fj, size=max(8, jp - 1))
        self.f_zh = tkfont.Font(family=fz, size=size, weight="normal")
        self.f_head = tkfont.Font(family=fz, size=max(9, gs), weight="bold")
        self.f_gram = tkfont.Font(family=fz, size=gs)
        self.f_meta = tkfont.Font(family="Consolas", size=8)

        self.win.configure(bg=theme["border"])
        self.body.configure(bg=theme["bg"])
        for w in (self.hdr,):
            w.configure(bg=theme["bg"])
        self.grip.configure(bg=theme["bg"], fg=theme["meta"])
        self.hint.configure(bg=theme["bg"], fg=theme["meta"])
        self.meta.configure(bg=theme["bg"], fg=theme["meta"], font=self.f_meta)
        self.text.configure(bg=theme["bg"], fg=theme["zh"], font=self.f_zh,
                            selectbackground="#2b4a6f", selectforeground="#ffffff")

        self.text.tag_configure("jp", font=self.f_jp, foreground=theme["jp"],
                                lmargin1=0, lmargin2=0, spacing3=2)
        self.text.tag_configure("kana", font=self.f_kana, foreground=theme["kana"], spacing3=2)
        self.text.tag_configure("zh", font=self.f_zh, foreground=theme["zh"],
                                spacing1=2, spacing3=4)
        self.text.tag_configure("sep", font=tkfont.Font(family=fz, size=4),
                                foreground=theme["border"])
        self.text.tag_configure("ghead", font=self.f_head, foreground=theme["gram_head"],
                                spacing1=4, spacing3=1)
        self.text.tag_configure("gram", font=self.f_gram, foreground=theme["gram"],
                                lmargin1=8, lmargin2=8, spacing3=1)
        self.text.tag_configure("vocab", font=self.f_gram, foreground=theme["gram"],
                                lmargin1=8, lmargin2=8, spacing3=1)
        self.text.tag_configure("note", font=self.f_gram, foreground=theme["note"],
                                lmargin1=0, lmargin2=0, spacing1=4)
        self.text.tag_configure("cur", font=self.f_zh, foreground=theme["note"])
        try:
            self.win.attributes("-alpha", float(self.cfg.get("overlay_alpha", 0.93)))
        except Exception:
            pass
        self._render()

    # ---------- 窗口 ----------
    def _hwnd_get(self) -> int:
        if self._hwnd is None:
            self.win.update_idletasks()
            self._hwnd = self.win.winfo_id()
        return self._hwnd

    def screen_rect(self) -> tuple[int, int, int, int] | None:
        """悬浮窗在屏幕上的 (x1, y1, x2, y2)；隐藏时返回 None。

        引擎截图时会把这块涂黑，否则贴在对话框上的译文会被自己 OCR 回来，
        导致每帧文本都在变、自动翻译永远判定不出「同一句」。
        """
        try:
            if not self.cfg.get("overlay_visible", True) or not self.win.winfo_exists():
                return None
            if self.win.state() == "withdrawn":
                return None
            r = winapi.window_rect(self._hwnd_get())
        except Exception:
            return None
        if not r:
            return None
        x1, y1, x2, y2 = r
        if x2 - x1 < 8 or y2 - y1 < 8:
            return None
        return r

    def show_window(self) -> None:
        self.win.deiconify()
        self.win.attributes("-topmost", True)
        self._place()
        self.cfg.set("overlay_visible", True)

    def hide_window(self) -> None:
        self.win.withdraw()
        self.cfg.set("overlay_visible", False)
        self.cfg.save()

    def toggle(self) -> None:
        if self.win.state() == "withdrawn":
            self.show_window()
        else:
            self.hide_window()

    def _pos(self, w: int, h: int) -> tuple[int, int]:
        """窗口左上角。手动拖过就用当前位置；没定位过则底部居中（字幕位）。"""
        if self._auto_pos is False and self._cur_pos:
            return int(self._cur_pos[0]), int(self._cur_pos[1])
        x, y = (self.cfg.get("overlay_pos") or [0, 0])[:2]
        x, y = int(x), int(y)
        if x == 0 and y == 0:
            try:
                _vx, _vy, sw, sh = winapi.virtual_screen()
                x = max(0, _vx + (sw - w) // 2)
                y = max(0, _vy + sh - h - 140)
            except Exception:
                pass
            self._auto_pos = True
        else:
            self._auto_pos = False
        # 只横向贴边；纵向交给 _fit_height 按「内容高度」处理（否则拖动时会被往下拽）。
        try:
            vx, vy, sw, sh = winapi.virtual_screen()
            x = min(max(x, vx), max(vx, vx + sw - w))
            y = min(max(y, vy), max(vy, vy + sh - 40))
        except Exception:
            pass
        self._cur_pos = [x, y]
        return x, y

    def avoid_rect(self, rect) -> bool:
        """如果自己压在识别区上，就挪开——否则自己挡住游戏文字（也挡住 OCR）。

        优先贴到识别区下沿之外（字幕条本来就在下方），下面放不下就放到上沿之外。
        返回是否挪过。挪完会落盘，用户下次打开还在这个位置。
        """
        try:
            r = self.screen_rect()
        except Exception:
            r = None
        if not r or not rect:
            return False
        x1, y1, x2, y2 = r
        rx, ry, rw, rh = (int(v) for v in rect[:4])
        if rw < 8 or rh < 8:
            return False
        if x2 <= rx or x1 >= rx + rw or y2 <= ry or y1 >= ry + rh:
            return False                                   # 没压住，什么都不用做
        win_w, win_h = x2 - x1, y2 - y1
        try:
            vx, vy, sw, sh = winapi.virtual_screen()
        except Exception:
            vx, vy, sw, sh = 0, 0, 1920, 1080
        ny = ry + rh + 6                                    # 识别区下方
        if ny + win_h > vy + sh:
            ny = ry - win_h - 6                             # 放不下就放上方
        if ny < vy:
            ny = max(vy, vy + sh - win_h)
        nx = min(max(x1, vx), max(vx, vx + sw - win_w))
        if abs(nx - x1) < 2 and abs(ny - y1) < 2:
            return False
        self.set_pos(nx, ny)
        return True

    def set_pos(self, x: int, y: int) -> None:
        """把窗口挪到 (x, y) 并立即落盘，之后的渲染不会再把它拉回旧位置。"""
        self._auto_pos = False
        self._cur_pos = [int(x), int(y)]
        w = int(self.cfg.get("overlay_width", 1100))
        h = max(90, int(self.win.winfo_height()))
        self._apply_geom(int(x), int(y), w, h)
        self.cfg.set("overlay_pos", [int(x), int(y)])
        try:
            self.cfg.save()
        except Exception:
            pass

    def reset_pos(self) -> None:
        """回到「屏幕底部居中 + 自动跟随高度」的默认摆放。"""
        self._auto_pos = None
        self._cur_pos = None
        self.cfg.set("overlay_pos", [0, 0])
        try:
            self.cfg.save()
        except Exception:
            pass
        self._fit_height()

    def _apply_geom(self, x: int, y: int, w: int, h: int, topmost: bool = False) -> None:
        """位置和尺寸都先交给 Tk（wm geometry），负坐标再补一次 Win32。

        只调 SetWindowPos 时 Tk 并不知道窗口被挪过：它下次 idle 会按自己的记录
        重新摆位（新窗口默认 0,0），于是窗口既停在左上角又拖不走。
        多显示器左侧的负坐标 Tk 会理解成「距右边缘」，这种才用 Win32 纠正。
        """
        x, y, w, h = int(x), int(y), int(w), int(h)
        negative = x < 0 or y < 0
        try:
            if negative:
                self.win.geometry(f"{w}x{h}")
            else:
                self.win.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass
        if topmost:
            try:
                winapi.place_window(self._hwnd_get(), x, y, w, h, topmost=True)
            except Exception:
                pass
        elif negative:
            try:
                winapi.move_window(self._hwnd_get(), x, y, w, h)
            except Exception:
                pass

    def _place(self) -> None:
        self.win.update_idletasks()
        w = int(self.cfg.get("overlay_width", 1100))
        h = max(90, self.win.winfo_reqheight())
        x, y = self._pos(w, h)
        self._apply_geom(x, y, w, h, topmost=True)

    def _fit_height(self) -> None:
        w = int(self.cfg.get("overlay_width", 1100))
        # 先按目标宽度摆一次，折行行数才是真实的（否则按 1 字符宽算出来是天量）
        x, y = self._pos(w, 300)
        self._apply_geom(x, y, w, 300)
        try:
            self.text.update_idletasks()
            # 带 update 参数：强制先排版再数显示行，否则内容变多后会数少，
            # 高度算小、底下的语法行就跑到屏幕外看不见了。
            lines = int(self.text.count("1.0", "end-1c", "update", "displaylines") or 1)
        except Exception:
            lines = int(self.text.index("end-1c").split(".")[0])
        lines = max(1, lines)
        h = 22 + lines * self.f_zh.metrics("linespace") + 14
        self.text.configure(height=lines)
        x, y = self._pos(w, int(h))    # 自动底部对齐时 y 随高度一起变
        # 内容长到屏幕放不下时，把底边贴到屏幕内（上移），否则「语法/生词」那几行会被屏幕切掉。
        # 只影响这一次摆放，不改 _cur_pos——否则松手落盘的是「被顶上去的位置」而不是你拖到的位置。
        try:
            vx, vy, sw, sh = winapi.virtual_screen()
            y = min(max(int(y), vy), max(vy, vy + sh - int(h) - 4))
        except Exception:
            pass
        self._apply_geom(x, y, w, int(h))

    def _toggle_lock(self) -> None:
        locked = not bool(self.cfg.get("overlay_locked"))
        self.cfg.set("overlay_locked", locked)
        self.cfg.save()
        try:
            winapi.set_click_through(self._hwnd_get(), locked)
        except Exception as exc:
            log.debug("穿透设置失败：%s", exc)
        self.hint.configure(text="已锁定（F6 解锁）" if locked else "")
        if self.on_lock:
            self.on_lock(locked)

    def apply_lock(self) -> None:
        locked = bool(self.cfg.get("overlay_locked"))
        try:
            winapi.set_click_through(self._hwnd_get(), locked)
        except Exception:
            pass
        self.hint.configure(text="已锁定（F6 解锁）" if locked else "")

    # ---------- 拖动 / 缩放 ----------
    def _on_press(self, ev):
        if self.cfg.get("overlay_locked"):
            return
        self._drag = (ev.x_root, ev.y_root,
                      int(self._cur_pos[0]) if self._cur_pos else int(self.win.winfo_x()),
                      int(self._cur_pos[1]) if self._cur_pos else int(self.win.winfo_y()),
                      "resize" if ev.widget is self.hdr else "move",
                      int(self.win.winfo_width()))

    def _on_drag(self, ev):
        if not self._drag:
            return
        x0, y0, wx, wy, mode, ww = self._drag
        dx, dy = ev.x_root - x0, ev.y_root - y0
        if mode == "resize":  # 拖表头空白处 = 调宽度
            neww = max(380, ww + dx)
            self.cfg.set("overlay_width", neww)
        nx, ny = wx + dx, wy + dy
        # 记住新位置：否则 _fit_height 会按 cfg 里的旧坐标把它搬回去
        self._auto_pos = False
        self._cur_pos = [int(nx), int(ny)]
        try:
            w = int(self.cfg.get("overlay_width", 1100))
            # 走 Tk 的 geometry，让 Tk 自己也认这个位置（否则松手后又被它摆回 0,0）
            if nx >= 0 and ny >= 0:
                self.win.geometry(f"{w}x{int(self.win.winfo_height())}+{int(nx)}+{int(ny)}")
            else:
                winapi.move_window(self._hwnd_get(), int(nx), int(ny), w,
                                   int(self.win.winfo_height()))
        except Exception:
            pass

    def _on_release(self, _ev):
        if self._drag:
            pos = self._cur_pos or [self.win.winfo_x(), self.win.winfo_y()]
            try:
                self.cfg.set("overlay_pos", [int(pos[0]), int(pos[1])])
                self.cfg.save()
            except Exception:
                pass
            self._drag = None
            self._render()

    def _on_wheel(self, ev):
        self._bump_font(1 if ev.delta > 0 else -1)

    def _on_menu(self, ev):
        if self.cfg.get("overlay_locked"):
            return
        try:
            self.menu.tk_popup(ev.x_root, ev.y_root)
        finally:
            self.menu.grab_release()

    def _bump_font(self, delta: int) -> None:
        self.cfg.set("zh_size", max(10, min(48, int(self.cfg.get("zh_size", 19)) + delta)))
        self.cfg.set("jp_size", max(8, min(32, int(self.cfg.get("jp_size", 12)) + delta // 2)))
        self.apply_config()
        self.cfg.save()

    def _copy_jp(self) -> None:
        self.win.clipboard_clear()
        self.win.clipboard_append(self._current.get("jp") or self._current.get("jp_ocr") or "")

    def _copy_zh(self) -> None:
        self.win.clipboard_clear()
        self.win.clipboard_append(self._current.get("zh") or "")

    # ---------- 渲染 ----------
    def show(self, res: dict) -> None:
        self._current = res or {}
        self._partial_n = 0
        self._render()
        if not self.cfg.get("overlay_visible", True):
            self.show_window()

    def show_partial(self, data: dict) -> None:
        """流式中间态：中文边生成边上屏，语法区显示进度。

        只为了「别让用户干等 3 秒」，所以做了节流：每 3 次增量才重新量一次高度，
        否则窗口尺寸每 70ms 抖一次，比慢更难看。
        """
        zh = data.get("zh") or ""
        if not zh:
            return
        self._current = {
            "jp": data.get("jp") or "",
            "zh": zh,
            "grammar": [],
            "vocab": [],
            "notes": "语法解析中…" if data.get("stage") == "grammar" else "",
            "_partial": True,
        }
        self._partial_n = getattr(self, "_partial_n", 0) + 1
        refit = data.get("stage") == "grammar" or self._partial_n % 3 == 1
        self._render(fit=refit)
        if not self.cfg.get("overlay_visible", True):
            self.show_window()

    def _render(self, fit: bool = True) -> None:
        res = self._current
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        if not res:
            t.insert("end", "等待台词…（把框选区域对准游戏文本框，按 F9 或开启自动）", "jp")
            t.configure(state="disabled")
            self.meta.configure(text="")
            self._fit_height()
            return

        jp = res.get("jp") or res.get("jp_ocr") or ""
        if self.cfg.get("show_jp", True) and jp:
            t.insert("end", jp + "\n", "jp")
        if self.cfg.get("show_kana", False) and res.get("kana"):
            t.insert("end", res["kana"] + "\n", "kana")
        zh = res.get("zh") or ""
        if zh:
            t.insert("end", zh, "zh")
            if res.get("_partial") and not res.get("notes"):
                # 流式光标：让「还在生成」一眼可见，而不是看起来卡住了
                t.insert("end", "▍", "cur")
            t.insert("end", "\n")

        gram = res.get("grammar") or []
        if gram:
            t.insert("end", "\n", "sep")
            t.insert("end", "语法\n", "ghead")
            # 悬浮窗只放一行摘要，详细讲解留给主窗口——否则一行台词能糊掉半屏游戏
            for g in gram[:3]:
                head = g.get("pattern", "")
                if g.get("level"):
                    head += f"　{g['level']}"
                if g.get("meaning"):
                    head += f"　{g['meaning']}"
                t.insert("end", "・" + head + "\n", "gram")

        vocab = res.get("vocab") or []
        if self.cfg.get("show_vocab", True) and vocab:
            t.insert("end", "\n", "sep")
            t.insert("end", "词\n", "ghead")
            for v in vocab[:4]:
                line = v.get("word", "")
                if v.get("reading"):
                    line += f"（{v['reading']}）"
                if v.get("meaning"):
                    line += f" {v['meaning']}"
                t.insert("end", "・" + line + "\n", "vocab")

        if self.cfg.get("show_notes", True) and res.get("notes"):
            note = str(res["notes"])
            if len(note) > 90:
                note = note[:90] + "…"
            t.insert("end", "　" + note + "\n", "note")

        t.configure(state="disabled")
        meta = res.get("meta") or {}
        bits = []
        if meta.get("cached"):
            bits.append("缓存")
        if meta.get("ms") is not None:
            bits.append(f"{meta['ms']}ms")
        if meta.get("engine"):
            bits.append(str(meta["engine"]).split(":")[-1])
        self.meta.configure(text="  ".join(bits))
        if fit:
            self._fit_height()

    def destroy(self) -> None:
        try:
            self.win.destroy()
        except Exception:
            pass
