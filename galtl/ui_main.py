"""主控窗口：框选区域、开关自动翻译、实时预览、手动解析。"""
from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from . import APP_NAME, __version__, winapi
from .config import Config
from .ocr import merge_lines
from .overlay_keys import HOTKEY_MAP
from .pipeline import Engine
from .store import Store
from .textutil import display_join, normalize
from .ui_cover import CoverWindow
from .ui_overlay import Overlay
from .ui_region import RegionSelector
from .ui_settings import SettingsDialog

log = logging.getLogger("galtl.ui")

MODES = [("ocr", "屏幕 OCR（离线内置）"), ("clipboard", "剪贴板（Textractor）"),
         ("vision", "视觉模型直读截图")]


class MainWindow:
    def __init__(self, root: tk.Tk, cfg: Config, store: Store, engine: Engine,
                 events: "queue.Queue"):
        self.root = root
        self.cfg = cfg
        self.store = store
        self.engine = engine
        self.events = events
        self.hk_queue: "queue.Queue[str]" = queue.Queue()
        self._manual_busy = False
        self._closing = False
        self._hotkeys: winapi.HotkeyThread | None = None

        self.root.title(f"{APP_NAME} v{__version__}")
        self.root.geometry("760x680")
        self.root.minsize(620, 520)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._style()
        self._build()
        self.overlay = Overlay(
            self.root, cfg,
            on_quit=self.on_close,
            on_settings=self.open_settings,
            on_lock=self._on_lock_changed,
        )
        self._start_hotkeys()
        # 截图时排除悬浮窗自身，否则贴在对话框上的译文会被 OCR 回来（自动翻译因此永不触发）
        self.engine.set_overlay_rect(self.overlay.screen_rect)
        # 覆盖原文：把中文直接盖在游戏原文上（系统级截图排除，见 ui_cover 模块注释）
        self.cover = CoverWindow(self.root, cfg)
        self.engine.cover = self.cover
        self._last_zh = ""          # 最近一次译文：换区域/开关覆盖时用来立刻重画
        self._shown_seq = 0         # 已经上屏的句子号：比它旧的流式增量直接丢（防两句译文闪动）
        self.cover.set_rect(self.engine.target_rect())
        if self.cfg.region[2] >= 8:
            self.engine.set_region(self.cfg.region)
        if self.cfg.get("cover_mode"):
            self.overlay.hide_window()   # 覆盖模式下默认收起底部字幕条，画面干净
        # 启动就先躲开识别区：等到第一帧 OCR 之后再挪，第一帧仍会读到自己的译文
        self.root.update_idletasks()
        if self.cfg.region[2] >= 8 and self.overlay.avoid_rect(self.cfg.region):
            self.cfg.save()
        self.root.after(80, self._pump)
        self.root.after(300, self._first_hint)
        self.root.after(1200, self._cover_keepalive)
        if self.cfg.get("auto_translate"):
            # 上次退出时「自动」是开着的，这次启动就直接跑起来。
            # 以前只在勾选框/F8 里启动，配置里写着自动、实际却没跑——用户看到的就是
            # 「换一句就得点一下 F9」。
            self.root.after(260, self._autostart)

    def _autostart(self) -> None:
        if self.var_auto.get() and not self.engine.auto_running:
            self.engine.start_auto()

    # ---------- 样式 ----------
    def _style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        base = tkfont.nametofont("TkDefaultFont")
        try:
            family = "Microsoft YaHei UI"
            base.configure(family=family, size=10)
            tkfont.nametofont("TkTextFont").configure(family=family, size=10)
        except Exception:
            pass
        bg = "#f4f6fa"
        self.root.configure(bg=bg)
        style.configure(".", background=bg, foreground="#1b2027")
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground="#1b2027")
        style.configure("Hint.TLabel", foreground="#6b7486")
        style.configure("TLabelframe", background=bg, bordercolor="#ccd4e0")
        style.configure("TLabelframe.Label", background=bg, foreground="#3a4453")
        style.configure("TButton", padding=(10, 6), relief="flat", background="#e6ebf3")
        style.map("TButton", background=[("active", "#d6dfec")])
        style.configure("Accent.TButton", background="#1f6feb", foreground="#ffffff",
                        padding=(12, 7))
        style.map("Accent.TButton", background=[("active", "#1858c4")])
        style.configure("TNotebook", background=bg, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(12, 6))
        style.configure("Status.TLabel", foreground="#4b5563")

    # ---------- 界面 ----------
    def _build(self) -> None:
        pad = dict(padx=12, pady=6)

        head = ttk.Frame(self.root)
        head.pack(fill="x", **pad)
        ttk.Label(head, text=APP_NAME, font=("Microsoft YaHei UI", 14, "bold")).pack(side="left")
        ttk.Label(head, text=f"v{__version__} · 纯本地 · 离线 OCR", style="Hint.TLabel").pack(
            side="left", padx=10
        )
        ttk.Button(head, text="设置", command=self.open_settings).pack(side="right")

        # --- 抓取目标 ---
        box1 = ttk.Labelframe(self.root, text="① 对准游戏文本框")
        box1.pack(fill="x", **pad)
        row = ttk.Frame(box1)
        row.pack(fill="x", padx=10, pady=8)
        self.lbl_region = ttk.Label(row, text=self._region_text(), width=30)
        self.lbl_region.pack(side="left")
        ttk.Button(row, text="框选区域（F7）", command=self.select_region).pack(side="left", padx=6)
        ttk.Button(row, text="预览截图", command=self.preview_shot).pack(side="left", padx=6)
        ttk.Label(row, text="识别方式").pack(side="left", padx=(18, 6))
        self.cb_mode = ttk.Combobox(row, state="readonly", width=22,
                                    values=[label for _k, label in MODES])
        self.cb_mode.current(self._mode_index())
        self.cb_mode.pack(side="left")
        self.cb_mode.bind("<<ComboboxSelected>>", self._on_mode)

        # --- 控制 ---
        box2 = ttk.Labelframe(self.root, text="② 翻译")
        box2.pack(fill="x", **pad)
        row2 = ttk.Frame(box2)
        row2.pack(fill="x", padx=10, pady=8)
        self.var_auto = tk.BooleanVar(value=bool(self.cfg.get("auto_translate")))
        ttk.Checkbutton(row2, text="自动翻译", variable=self.var_auto,
                        command=self.toggle_auto).pack(side="left")
        ttk.Label(row2, text="间隔").pack(side="left", padx=(14, 4))
        self.var_poll = tk.StringVar(value=str(int(self.cfg.get("poll_ms", 700))))
        ttk.Spinbox(row2, from_=200, to=5000, increment=100, width=6,
                    textvariable=self.var_poll, command=self._on_poll).pack(side="left")
        ttk.Label(row2, text="ms").pack(side="left", padx=(2, 12))
        ttk.Button(row2, text="立即翻译（F9）", style="Accent.TButton",
                   command=self.translate_now).pack(side="left")
        ttk.Button(row2, text="字幕显隐（F10）",
                   command=lambda: self.overlay.toggle()).pack(side="left", padx=8)
        self.var_cover = tk.BooleanVar(value=bool(self.cfg.get("cover_mode")))
        ttk.Checkbutton(row2, text="覆盖原文（F11）", variable=self.var_cover,
                        command=self.toggle_cover).pack(side="left")
        self.var_lock = tk.BooleanVar(value=bool(self.cfg.get("overlay_locked")))
        ttk.Checkbutton(row2, text="鼠标穿透（F6）", variable=self.var_lock,
                        command=self.toggle_lock).pack(side="left")

        # --- 结果 ---
        box3 = ttk.Labelframe(self.root, text="③ 结果 / 语法解析")
        box3.pack(fill="both", expand=True, **pad)
        self.txt = tk.Text(box3, wrap="word", bd=0, highlightthickness=1,
                           highlightbackground="#dbe2ec", bg="#ffffff", fg="#1b2027",
                           padx=12, pady=10, spacing3=3)
        sb = ttk.Scrollbar(box3, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(fill="both", expand=True, padx=(8, 0), pady=8)
        self.txt.tag_configure("jp", font=("Yu Gothic UI", 12), foreground="#5b6675")
        self.txt.tag_configure("zh", font=("Microsoft YaHei UI", 13, "bold"), foreground="#0f1720",
                               spacing1=6, spacing3=6)
        self.txt.tag_configure("ghead", font=("Microsoft YaHei UI", 10, "bold"),
                               foreground="#0f7a6a", spacing1=8)
        self.txt.tag_configure("gram", font=("Microsoft YaHei UI", 10), foreground="#28313d",
                               lmargin1=10, lmargin2=10)
        self.txt.tag_configure("note", font=("Microsoft YaHei UI", 10), foreground="#9a6a12",
                               spacing1=6)
        self.txt.tag_configure("meta", font=("Consolas", 8), foreground="#8a94a4")
        self.txt.configure(state="disabled")

        # --- 手动输入 ---
        box4 = ttk.Labelframe(self.root, text="④ 手动解析（粘贴日文，回车发送）")
        box4.pack(fill="x", **pad)
        row4 = ttk.Frame(box4)
        row4.pack(fill="x", padx=10, pady=8)
        self.entry = ttk.Entry(row4, font=("Yu Gothic UI", 11))
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda _e: self.parse_manual())
        ttk.Button(row4, text="解析", command=self.parse_manual).pack(side="left", padx=8)
        ttk.Button(row4, text="粘贴解析", command=self.parse_clipboard).pack(side="left")

        # --- 状态栏 ---
        bar = ttk.Frame(self.root)
        bar.pack(fill="x", padx=12, pady=(0, 10))
        self.lbl_status = ttk.Label(bar, text="就绪", style="Status.TLabel")
        self.lbl_status.pack(side="left")
        self.lbl_stat = ttk.Label(bar, text="", style="Status.TLabel")
        self.lbl_stat.pack(side="right")
        ttk.Button(bar, text="清屏", command=self.clear).pack(side="right", padx=8)

    # ---------- 辅助 ----------
    def _region_text(self) -> str:
        pid = int(self.cfg.get("target_pid", 0) or 0)
        if pid:
            name = self.cfg.get("target_exe") or self.cfg.get("target_title") or f"pid {pid}"
            return f"跟随程序：{name}"
        x, y, w, h = self.cfg.region
        if w < 8 or h < 8:
            return "未框选区域"
        return f"x={x} y={y}  {w}×{h}"

    def _mode_index(self) -> int:
        mode = self.cfg.get("mode", "ocr")
        for i, (k, _l) in enumerate(MODES):
            if k == mode:
                return i
        return 0

    def _first_hint(self) -> None:
        if self.cfg.region[2] >= 8 and self.overlay.avoid_rect(self.cfg.region):
            # 字幕条压在自己的识别区上：先挪开，否则 OCR 只能读到被挡剩下的半截
            self.cfg.save()
        if self.cfg.region[2] < 8:
            self.set_status("先点「框选区域」，把矩形拖在游戏对话框上")
        elif self._region_advice(self.cfg.region):
            self.set_status(self._region_advice(self.cfg.region))
        elif not self.cfg.has_key:
            self.set_status("未配置模型 API Key：设置 → 模型；现在仍可看本地语法提示")

    def _region_advice(self, rect) -> str:
        """框选过大时给出可执行的建议：整屏一帧要 1~3 秒，对话框只要 0.2 秒。"""
        try:
            _x, _y, w, h = (int(v) for v in rect)
        except Exception:
            return ""
        area = max(1, w * h)
        sx, sy, sw, sh = winapi.virtual_screen()
        # region 存的是物理像素，virtual_screen() 给的是逻辑尺寸，按 DPI 换算回去
        try:
            d = max(1.0, float(winapi.dpi_scale()))
        except Exception:
            d = 1.0
        screen = max(1, int(sw * d) * int(sh * d))
        if area > 0.45 * screen:
            return f"框选几乎整屏（{w}×{h}）：一帧要 1~3 秒，按 F7 只框对话框可降到约 0.2 秒"
        return ""

    def set_status(self, text: str) -> None:
        self.lbl_status.configure(text=text)

    def _start_hotkeys(self) -> None:
        if not self.cfg.get("hotkeys", True):
            return
        try:
            self._hotkeys = winapi.HotkeyThread(HOTKEY_MAP, self.hk_queue)
            self._hotkeys.start()
            self._hotkeys.wait_ready(2.0)
            if self._hotkeys.error:
                self.set_status(self._hotkeys.error)
        except Exception as exc:
            log.debug("热键启动失败：%s", exc)

    # ---------- 交互 ----------
    def select_region(self) -> None:
        self.root.withdraw()
        self.root.update_idletasks()
        self.root.after(120, self._do_select)

    def _do_select(self) -> None:
        try:
            rect = RegionSelector(self.root).select()
        finally:
            self.root.deiconify()
        if rect:
            self.cfg.set("region", list(rect))
            # 手动框选即「我就要这块」：清掉之前选的识别目标程序，否则每轮
            # target_rect() 又会按进程取整个客户区，手框等于白框。
            self.cfg.set("target_pid", 0)
            self.cfg.set("target_exe", "")
            self.cfg.set("target_title", "")
            self.cfg.save()
            self.lbl_region.configure(text=self._region_text())
            self.cover.set_rect(self.engine.target_rect())
            self.engine.set_region(self.cfg.region)   # 记下这块像素属于哪个窗口
            if self.cover.visible and self._last_zh:
                self.cover.show(self._last_zh)
            moved = self.overlay.avoid_rect(rect)
            hint = self._region_advice(rect)
            self.set_status(hint or f"区域已设置 {rect}")
            if moved:
                self.set_status(f"字幕条挡住了识别区，已自动挪开；{hint or ''}".strip())
            self.translate_now()
        else:
            self.set_status("已取消框选")

    def preview_shot(self) -> None:
        x, y, w, h = self.cfg.region
        if w < 8:
            messagebox.showinfo("提示", "先框选字幕区域")
            return
        try:
            png, pw, ph = winapi.grab_png(
                x, y, w, h, scale=float(self.cfg.get("upscale", 2.0)),
                binarize=bool(self.cfg.get("binarize", False)),
            )
        except Exception as exc:
            messagebox.showerror("截图失败", str(exc))
            return
        top = tk.Toplevel(self.root)
        top.title(f"截图预览 {pw}×{ph}（送 OCR 的图）")
        img = tk.PhotoImage(data=png, format="png")
        lbl = tk.Label(top, image=img, bg="#222")
        lbl.image = img  # 防止被 GC
        lbl.pack()
        info = tk.Label(top, text="识别中…", anchor="w", justify="left",
                        font=("Microsoft YaHei UI", 10))
        info.pack(fill="x", padx=8, pady=6)

        def run():
            try:
                lines = self.engine.ocr().recognize_png(png)
                text = normalize(display_join(merge_lines(lines)))
                info.configure(text="OCR 结果：\n" + (text or "（空）"))
            except Exception as exc:
                info.configure(text=f"OCR 失败：{exc}")

        threading.Thread(target=run, daemon=True).start()

    def _on_mode(self, _ev=None) -> None:
        key = MODES[self.cb_mode.current()][0]
        self.cfg.set("mode", key)
        self.cfg.save()
        note = {
            "ocr": "屏幕 OCR 模式：靠框选区域取字",
            "clipboard": "剪贴板模式：需 Textractor / 游戏内复制功能把台词放进剪贴板",
            "vision": "视觉模型模式：截图直接交给多模态模型，需在设置里填「视觉模型」",
        }[key]
        self.set_status(note)

    def _on_poll(self) -> None:
        try:
            v = max(200, min(5000, int(float(self.var_poll.get()))))
        except Exception:
            v = 700
        self.cfg.set("poll_ms", v)
        self.cfg.save()

    def toggle_auto(self) -> None:
        if self.var_auto.get():
            self.cfg.set("auto_translate", True)
            self.cfg.save()
            self.engine.start_auto()
            self.set_status("自动翻译：开")
        else:
            self.cfg.set("auto_translate", False)
            self.cfg.save()
            self.engine.stop_auto()
            self.set_status("自动翻译：关")

    def toggle_cover(self) -> None:
        """覆盖原文：把中文直接盖在游戏原文的位置上，画面看起来就是官方中文。"""
        on = self.cover.toggle()
        self.var_cover.set(on)
        if on:
            self.overlay.hide_window()
            self._cover_keepalive()
            mode = getattr(self.cover, "capture_mode", "exclude")
            if not self.cfg.region:
                self.set_status("覆盖原文：开 —— 但还没框选。按 F7 框住对话框文字区（覆盖层要贴着它）")
            elif self._game_is_fullscreen():
                self.set_status(
                    "覆盖原文：开 —— 游戏正铺满全屏。独占全屏下 Windows 不允许任何窗口盖上去，"
                    "若看不到中文，把游戏画面设置改成「窗口化」或「无边框窗口」"
                )
            elif mode == "record":
                self.set_status("覆盖原文：开 · 录制模式（NVIDIA/OBS 能录到中文，取词瞬间自动隐身）")
            elif getattr(self.cover, "excluded", False):
                self.set_status("覆盖原文：开（译文盖住日文，日文仍能被读取，不影响自动翻译）")
            else:
                self.set_status("覆盖原文：开（本机不支持截图排除，取样瞬间会闪一下）")
        else:
            self.overlay.show_window()
            self.set_status("覆盖原文：关（恢复底部字幕条）")

    def _game_is_fullscreen(self) -> bool:
        """游戏窗口是否铺满整块屏幕（全屏时任何覆盖层都盖不上去）。"""
        try:
            hwnd = int(getattr(self.engine, "_game_hwnd", 0) or 0)
            return bool(hwnd) and winapi.is_fullscreen_window(hwnd)
        except Exception:
            return False

    def _cover_keepalive(self) -> None:
        """每 1 秒把覆盖层重新顶到最上面。

        置顶窗口之间也有先后：游戏一被激活（点画面、alt-tab 回来）就把自己提到队首，
        覆盖层随之被压在下面——现象就是「一点游戏，中文就被盖住」。这里轮询重提，
        只在真正需要时（覆盖层可见且前台不是自己）才调用，开销是一次 SetWindowPos。
        """
        if self._closing:
            return
        try:
            # 内嵌：跟着游戏窗口走（移动/缩放/最小化），顺便重提一次置顶。
            self.cover.follow()
            if self.cover.visible and self._last_zh:
                self.cover.raise_above()
        except Exception:
            pass
        try:
            self.root.after(1000, self._cover_keepalive)
        except Exception:
            pass

    def toggle_lock(self) -> None:
        self.cfg.set("overlay_locked", bool(self.var_lock.get()))
        self.cfg.save()
        self.overlay.apply_lock()

    def _on_lock_changed(self, locked: bool) -> None:
        self.var_lock.set(locked)

    def translate_now(self) -> None:
        if self.engine.auto_running:
            self.engine._last_key = ""      # 强制重抓一次
        if self._manual_busy:
            return
        self._manual_busy = True
        self.set_status("抓取中…")

        def run():
            try:
                self.engine.run_once(force=True)
            finally:
                self._manual_busy = False

        threading.Thread(target=run, daemon=True).start()

    def parse_manual(self) -> None:
        text = normalize(self.entry.get())
        if not text:
            return
        self.entry.delete(0, "end")
        self.set_status("解析中…")

        def run():
            self.engine.translate_free(text)

        threading.Thread(target=run, daemon=True).start()

    def parse_clipboard(self) -> None:
        try:
            text = normalize(self.root.clipboard_get())
        except Exception:
            text = ""
        if not text:
            self.set_status("剪贴板里没有文本")
            return
        self.entry.delete(0, "end")
        self.entry.insert(0, text)
        self.parse_manual()

    def clear(self) -> None:
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")

    def open_settings(self) -> None:
        SettingsDialog(self.root, self.cfg, on_saved=self._after_settings,
                       store=self.store, engine=self.engine)

    def _after_settings(self) -> None:
        self._on_poll()
        self.var_lock.set(bool(self.cfg.get("overlay_locked")))
        self.overlay.apply_config()
        self.overlay.apply_lock()
        # 引擎侧需要重建 OCR（引擎类型可能变）
        if self.engine._ocr is not None:
            try:
                self.engine._ocr.close()
            except Exception:
                pass
            self.engine._ocr = None
        try:
            self.cover.apply_theme()
        except Exception:
            pass
        # 截图可见性可能被改（exclude / record / off）：立刻生效，别等重启
        try:
            self.cover.set_capture_mode(self.cfg.get("cover_capture", "exclude"))
        except Exception:
            pass
        # 内嵌开关也可能被改
        try:
            self.cover.set_embed(bool(self.cfg.get("cover_embed", True)))
        except Exception:
            pass
        # 识别目标程序可能换了：重认一次窗口，并让覆盖层绑到新的那一个
        try:
            self.engine.set_region()
            self.cover.set_rect(self.engine.target_rect())
            if self.cover.visible:
                self.cover.detect_game()
                self.cover.bind_game()
        except Exception:
            log.debug("切换识别目标后重绑失败", exc_info=True)
        if int(self.cfg.get("target_pid", 0) or 0):
            self.lbl_region.configure(text="跟随程序窗口")
            self.set_status(f"已跟随程序：{self.cfg.get('target_exe') or self.cfg.get('target_title')}")
            self.translate_now()
        self.set_status("设置已应用")

    # ---------- 事件循环 ----------
    def _pump(self) -> None:
        # 热键
        while True:
            try:
                name = self.hk_queue.get_nowait()
            except queue.Empty:
                break
            if name == "F7":
                self.select_region()
            elif name == "F8":
                self.var_auto.set(not self.var_auto.get())
                self.toggle_auto()
            elif name == "F9":
                self.translate_now()
            elif name == "F10":
                self.overlay.toggle()
            elif name == "F11":
                self.var_cover.set(not self.var_cover.get())
                self.toggle_cover()
            elif name == "F6":
                self.var_lock.set(not self.var_lock.get())
                self.toggle_lock()

        # 引擎事件
        while True:
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "result":
                self._show_result(data)
            elif kind == "partial":
                # 上一句的流式残片不许上屏：seq 比当前已显示的更旧就直接丢掉。
                # 否则「玩家已经翻到下一句，上一句的最后几个字才流回来」，
                # 屏幕就在两句不同译文之间来回闪。
                seq = data.get("seq")
                if seq is not None and self._shown_seq and int(seq) < int(self._shown_seq):
                    log.debug("丢弃过期增量 seq=%s 当前=%s", seq, self._shown_seq)
                    continue
                self.overlay.show_partial(data)
                if data.get("zh"):
                    self.cover.show(data["zh"], getattr(self.engine, "_glyph_px", 0.0))   # 边译边盖：不用等整句
                if data.get("stage") == "grammar":
                    self.set_status("译文已出，正在补语法讲解…")
                else:
                    self.set_status(f"译文生成中… {data.get('ms', 0)}ms")
            elif kind == "status":
                self.set_status(str(data))
            elif kind == "error":
                self.set_status("⚠ " + str(data))
                log.warning("引擎错误：%s", data)
            elif kind == "ocr":
                self.lbl_stat.configure(text=f"OCR {data.get('ms', 0)}ms")

        s = self.engine.stats
        if s["ocr"] or s["cache"]:
            self.lbl_stat.configure(
                text=f"OCR {s['ocr']} · 模型 {s['llm']} · 缓存 {s['cache']} · 失败 {s['fail']}"
            )
        self.root.after(90, self._pump)

    def _show_result(self, res: dict) -> None:
        if res.get("noise"):
            self.set_status("这一帧不像台词，已跳过")
            return
        self.overlay.show(res)
        self._last_zh = res.get("zh") or ""
        self.cover.show(self._last_zh, getattr(self.engine, "_glyph_px", 0.0))
        seq = (res.get("meta") or {}).get("seq")
        if seq is not None:
            self._shown_seq = int(seq)      # 之后比它更旧的流式残片一律丢掉
        # 语法讲解是异步补上的：同一句要「替换」上一块，不能又追加一遍
        jp = (res.get("jp") or res.get("jp_ocr") or "").strip()
        replace = bool(res.get("enrich")) and jp == getattr(self, "_last_block_jp", None)
        self._render_preview(res, replace_last=replace)

    def _render_preview(self, res: dict, replace_last: bool = False) -> None:
        t = self.txt
        t.configure(state="normal")
        if replace_last and getattr(self, "_last_block_start", None):
            try:
                t.delete(self._last_block_start, "end")
            except Exception:
                pass
        self._last_block_start = t.index("end-1c")
        jp = res.get("jp") or res.get("jp_ocr") or ""
        self._last_block_jp = jp.strip()
        if jp:
            t.insert("end", jp + "\n", "jp")
        if res.get("kana") and self.cfg.get("show_kana", False):
            t.insert("end", res["kana"] + "\n", "jp")
        if res.get("zh"):
            t.insert("end", res["zh"] + "\n", "zh")
        if res.get("literal"):
            t.insert("end", "直译：" + res["literal"] + "\n", "jp")
        gram = res.get("grammar") or []
        if gram:
            t.insert("end", "语法\n", "ghead")
            for g in gram:
                head = g.get("pattern", "")
                extra = "　".join(x for x in [g.get("level"), g.get("meaning")] if x)
                if extra:
                    head += "　" + extra
                t.insert("end", "・" + head + "\n", "gram")
                if g.get("form"):
                    t.insert("end", "　句片段：" + g["form"] + "\n", "gram")
                if g.get("explain"):
                    t.insert("end", "　" + g["explain"] + "\n", "gram")
        if res.get("vocab"):
            t.insert("end", "重点词\n", "ghead")
            for v in res["vocab"][:8]:
                line = v.get("word", "")
                if v.get("reading"):
                    line += f"（{v['reading']}）"
                if v.get("meaning"):
                    line += " " + v["meaning"]
                t.insert("end", "・" + line + "\n", "gram")
        if res.get("notes"):
            t.insert("end", "语气/语境：" + res["notes"] + "\n", "note")
        meta = res.get("meta") or {}
        bits = [f"{meta.get('ms', 0)}ms", str(meta.get("engine", ""))]
        if meta.get("cached"):
            bits.append("缓存命中")
        t.insert("end", " ".join(b for b in bits if b) + "\n\n", "meta")
        t.see("1.0")   # 原文与译文在顶部，每次新结果回到顶部更好读
        t.configure(state="disabled")

    # ---------- 关闭 ----------
    def on_close(self) -> None:
        self._closing = True
        try:
            self.cfg.set("auto_translate", bool(self.var_auto.get()))
            self.cfg.save()
        except Exception:
            pass
        try:
            if self._hotkeys:
                self._hotkeys.stop()
        except Exception:
            pass
        try:
            self.cover.destroy()
        except Exception:
            pass
        try:
            self.engine.close()
        except Exception:
            pass
        try:
            self.store.close()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
