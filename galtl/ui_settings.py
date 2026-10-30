"""设置窗口：模型 / 抓取 / 显示 / 缓存与热键。"""
from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import llm, winapi


class SettingsDialog:
    def __init__(self, master: tk.Tk, cfg, on_saved=None, store=None, engine=None):
        self.cfg = cfg
        self.on_saved = on_saved
        self.store = store
        self.engine = engine
        self.win = tk.Toplevel(master)
        self.win.title("设置 · galgame 日语助手")
        self.win.geometry("720x620")
        self.win.minsize(640, 560)
        self.win.transient(master)
        try:
            from . import uiicon

            uiicon.apply(self.win)
        except Exception:
            pass
        self.vars: dict[str, tk.Variable] = {}
        self._provider_keys = list(llm.PROVIDERS.keys())
        self._build()
        self.win.grab_set()
        self.win.focus_set()

    # ---------- 工具 ----------
    def _var(self, key, kind="str"):
        inst = {"str": tk.StringVar, "int": tk.IntVar, "bool": tk.BooleanVar,
                "dbl": tk.DoubleVar}[kind]
        v = inst(value=self.cfg.get(key))
        self.vars[key] = v
        return v

    def _row(self, parent, r, label, widget, hint=None):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", padx=(12, 8), pady=6)
        widget.grid(row=r, column=1, sticky="ew", padx=(0, 12), pady=6)
        if hint:
            ttk.Label(parent, text=hint, foreground="#7a8494").grid(
                row=r, column=2, sticky="w", padx=(0, 12)
            )
        return widget

    # ---------- 构建 ----------
    def _build(self):
        # 按钮行先 pack（side="bottom"），Notebook 后 pack：
        # pack 是按调用顺序分配空间的，先把 Notebook 放进去再放按钮行的话，
        # Notebook 的 expand=True 会把高度全吃掉，按钮行被挤到窗口外——
        # 表现就是「一打开设置看不到保存并应用，得把窗口拉大」。
        bar = ttk.Frame(self.win)
        bar.pack(side="bottom", fill="x", padx=12, pady=(6, 12))
        self.btn_reset = ttk.Button(bar, text="恢复默认", command=self._reset)
        self.btn_reset.pack(side="left")
        self.btn_cancel = ttk.Button(bar, text="取消", command=self._close)
        self.btn_cancel.pack(side="right")
        self.btn_save = ttk.Button(bar, text="保存并应用", command=self._save)
        self.btn_save.pack(side="right", padx=8)
        self.lbl_verify = ttk.Label(bar, text="", foreground="#7a8494")
        self.lbl_verify.pack(side="right", padx=10)
        self._buttons = [self.btn_reset, self.btn_cancel, self.btn_save]
        ttk.Separator(self.win, orient="horizontal").pack(
            side="bottom", fill="x", padx=12)

        nb = ttk.Notebook(self.win)
        nb.pack(side="top", fill="both", expand=True, padx=10, pady=(10, 8))
        self._nb = nb
        self._tab_engine(nb)
        self._tab_model(nb)
        self._tab_capture(nb)
        self._tab_display(nb)
        self._tab_misc(nb)
        self._fit_window()

    def _fit_window(self):
        """把窗口调到「内容刚好放得下」，并按屏幕夹一下。

        150% 缩放 + 5 个页签时，内容往往比 720x620 高；窗口开小了按钮行
        虽然用 side="bottom" 保住了，但 Notebook 里的输入框会被切掉。
        这里按 Notebook 自己报的 reqheight 算一次，屏幕上放不下才允许滚动。
        """
        try:
            self.win.update_idletasks()
            sw = self.win.winfo_screenwidth()
            sh = self.win.winfo_screenheight()
            need_w = max(760, self.win.winfo_reqwidth())
            need_h = self.win.winfo_reqheight()
            # 屏幕尺寸在开了缩放的机器上可能是逻辑值，留足余量
            w = int(min(need_w, sw * 0.92))
            h = int(min(need_h, sh * 0.88))
            h = max(h, 520)
            self.win.geometry(f"{w}x{h}")
            self.win.minsize(int(min(660, w)), 480)
            if need_h > h:
                self._make_tabs_scrollable(need_h - h)
        except tk.TclError:
            pass

    def _make_tabs_scrollable(self, overflow: int) -> None:
        """屏幕实在放不下时，给每个页签套一层可滚动的画布。

        Tk 没有内置的滚动 Frame，常规做法是 Canvas + 内嵌窗口。
        只有当内容真的超出屏幕才套，避免平时多一层鼠标滚轮行为。
        """
        try:
            pages = list(self._nb.tabs())
        except tk.TclError:
            return
        for name in pages:
            inner = self.win.nametowidget(name)
            if getattr(inner, "_wrapped", False):
                continue
            canvas = tk.Canvas(self._nb, highlightthickness=0, borderwidth=0)
            canvas.configure(background=self._canvas_bg())
            vs = ttk.Scrollbar(self._nb, orient="vertical", command=canvas.yview)
            canvas.configure(yscrollcommand=vs.set)
            text = str(self._nb.tab(name, "text"))
            self._nb.forget(name)
            holder = ttk.Frame(self._nb)
            holder._wrapped = True                       # type: ignore[attr-defined]
            canvas.create_window((0, 0), window=inner, anchor="nw")
            canvas.pack(side="left", fill="both", expand=True)
            vs.pack(side="right", fill="y")
            inner.bind("<Configure>",
                       lambda e, c=canvas: c.configure(scrollregion=c.bbox("all")))
            for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                canvas.bind_all(
                    seq,
                    lambda e, c=canvas: c.yview_scroll(
                        int(-1 * (e.delta / 120)) if getattr(e, "delta", 0)
                        else (-1 if getattr(e, "num", 5) == 4 else 1), "units"),
                )
            self._nb.add(holder, text=text)

    def _canvas_bg(self) -> str:
        try:
            return str(ttk.Style().lookup("TFrame", "background") or "#20242b")
        except tk.TclError:
            return "#20242b"

    def _tab_engine(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="翻译引擎")
        f.columnconfigure(1, weight=1)

        box = ttk.LabelFrame(f, text="用哪个引擎翻译台词")
        box.grid(row=0, column=0, columnspan=3, sticky="ew", padx=12, pady=(10, 8))
        self.var_engine = tk.StringVar(value=str(self.cfg.get("translate_engine", "llm")))
        ttk.Radiobutton(
            box, text="外部 API（大模型）—— 译文 + 语法讲解", value="llm",
            variable=self.var_engine, command=self._sync_engine_state,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 2))
        ttk.Label(
            box, text="用「模型」页配的 OpenAI 兼容接口：一次请求同时拿译文、语法点、生词。按 token 计费。",
            foreground="#7a8494", wraplength=560, justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=(30, 8), pady=(0, 8))
        ttk.Radiobutton(
            box, text="微软翻译 —— 只给译文（语法/生词用本地句型库）", value="ms",
            variable=self.var_engine, command=self._sync_engine_state,
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 2))
        ttk.Label(
            box, text="Azure 翻译器 v3：按字符计费、每月免费 200 万字符；不花 token，但不会讲语法。",
            foreground="#7a8494", wraplength=560, justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=(30, 8), pady=(0, 8))

        self.ms_key = self._row(f, 1, "微软 Key",
                                ttk.Entry(f, textvariable=self._var("ms_key"), show="•"),
                                "Azure 门户「密钥和终结点」里的 KEY 1")
        self.ms_region = self._row(f, 2, "区域",
                                   ttk.Entry(f, textvariable=self._var("ms_region")),
                                   "如 eastasia；全球资源可留空")
        self.ms_endpoint = self._row(f, 3, "接口地址",
                                     ttk.Entry(f, textvariable=self._var("ms_endpoint")),
                                     "一般不用改")

        self.btn_ms_test = ttk.Button(f, text="测试微软翻译", command=self._test_ms)
        self.btn_ms_test.grid(row=4, column=1, sticky="w", pady=(8, 6))
        self.lbl_ms_test = ttk.Label(f, text="", foreground="#3a8f5c")
        self.lbl_ms_test.grid(row=4, column=2, sticky="w", pady=(8, 6))

        ttk.Label(
            f, text="切换后点「保存并应用」立刻生效；两条路可以随时来回切，缓存共用。",
            foreground="#7a8494",
        ).grid(row=5, column=0, columnspan=3, sticky="w", padx=12, pady=(4, 10))
        self._sync_engine_state()

    def _sync_engine_state(self):
        """只有选了微软翻译，才让 Azure 那几个输入框可用。"""
        on = self.var_engine.get() == "ms"
        for w in (self.ms_key, self.ms_region, self.ms_endpoint):
            try:
                w.configure(state="normal" if on else "disabled")
            except Exception:
                pass
        try:
            self.btn_ms_test.configure(state="normal" if on else "disabled")
        except Exception:
            pass

    def _test_ms(self):
        from . import msmt

        self.lbl_ms_test.configure(text="测试中…", foreground="#7a8494")
        self.win.update_idletasks()
        try:
            out = msmt.translate(
                "こんな時間まで起きているなんて、明日の朝が心配だよ。",
                key=self.vars["ms_key"].get(),
                region=self.vars["ms_region"].get(),
                endpoint=self.vars["ms_endpoint"].get(),
                timeout=min(30, int(self.vars["timeout"].get() or 20)),
            )
            self.lbl_ms_test.configure(text=f"OK：{out[:28]}", foreground="#3a8f5c")
        except Exception as exc:
            self.lbl_ms_test.configure(text=f"失败：{str(exc)[:60]}", foreground="#c0392b")

    def _tab_model(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="模型")
        f.columnconfigure(1, weight=1)

        prov = ttk.Combobox(
            f, state="readonly",
            values=[f"{k} · {v['label']}" for k, v in llm.PROVIDERS.items()],
            width=42,
        )
        cur = self.cfg.get("provider", "deepseek")
        idx = self._provider_keys.index(cur) if cur in self._provider_keys else 0
        prov.current(idx)
        self._row(f, 0, "渠道", prov)
        prov.bind("<<ComboboxSelected>>", lambda _e: self._on_provider(prov.current()))

        self.e_base = ttk.Entry(f, textvariable=self._var("base_url"))
        self._row(f, 1, "base_url", self.e_base, "到 /v1 为止")

        self.e_model = ttk.Entry(f, textvariable=self._var("model"))
        self._row(f, 2, "文本模型", self.e_model)

        self.e_vmodel = ttk.Entry(f, textvariable=self._var("vision_model"))
        self._row(f, 3, "视觉模型", self.e_vmodel, "vision 模式用")

        self.e_key = ttk.Entry(f, textvariable=self._var("api_key"), show="•")
        self._row(f, 4, "API Key", self.e_key, "仅存本机 config.json")

        self.e_gloss = tk.Text(f, height=5, wrap="word")
        self.e_gloss.grid(row=5, column=1, sticky="ew", padx=(0, 12), pady=6)
        ttk.Label(f, text="术语表").grid(row=5, column=0, sticky="nw", padx=(12, 8), pady=6)
        ttk.Label(f, text="每行 日文=中文\n人名/称呼优先", foreground="#7a8494",
                  justify="left").grid(row=5, column=2, sticky="nw", padx=(0, 12), pady=6)
        self.e_gloss.insert("1.0", self.cfg.get("glossary") or "")

        ttk.Label(f, text="温度").grid(row=6, column=0, sticky="w", padx=(12, 8), pady=6)
        ttk.Spinbox(f, from_=0.0, to=1.5, increment=0.1, width=8,
                    textvariable=self._var("temperature", "dbl")).grid(
            row=6, column=1, sticky="w", pady=6)
        ttk.Label(f, text="0.2~0.4 更稳定").grid(row=6, column=2, sticky="w")

        ttk.Label(f, text="超时(秒)").grid(row=7, column=0, sticky="w", padx=(12, 8), pady=6)
        ttk.Spinbox(f, from_=15, to=300, increment=5, width=8,
                    textvariable=self._var("timeout", "int")).grid(
            row=7, column=1, sticky="w", pady=6)

        ttk.Label(f, text="上文条数").grid(row=8, column=0, sticky="w", padx=(12, 8), pady=6)
        ttk.Spinbox(f, from_=0, to=6, width=8,
                    textvariable=self._var("context_lines", "int")).grid(
            row=8, column=1, sticky="w", pady=6)
        ttk.Label(f, text="带几条前文帮助判断指代").grid(row=8, column=2, sticky="w")

        ttk.Button(f, text="测试连接", command=self._test).grid(
            row=9, column=1, sticky="w", pady=(10, 6))
        self.lbl_test = ttk.Label(f, text="", foreground="#3a8f5c")
        self.lbl_test.grid(row=9, column=2, sticky="w", pady=(10, 6))

    def _tab_capture(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="抓取")
        f.columnconfigure(1, weight=1)

        mode = ttk.Combobox(f, state="readonly", width=28,
                            values=["ocr · 屏幕 OCR（离线内置）",
                                    "clipboard · 剪贴板（配 Textractor）",
                                    "vision · 视觉模型直读截图"])
        order = {"ocr": 0, "clipboard": 1, "vision": 2}
        mode.current(order.get(self.cfg.get("mode", "ocr"), 0))
        self._row(f, 0, "识别方式", mode)
        self._mode_cb = mode

        ttk.Label(f, text="间隔(ms)").grid(row=1, column=0, sticky="w", padx=(12, 8), pady=6)
        ttk.Spinbox(f, from_=200, to=5000, increment=100, width=8,
                    textvariable=self._var("poll_ms", "int")).grid(
            row=1, column=1, sticky="w", pady=6)
        ttk.Label(f, text="越小越灵敏，越费 CPU").grid(row=1, column=2, sticky="w")

        ttk.Label(f, text="稳定确认次数").grid(row=2, column=0, sticky="w", padx=(12, 8), pady=6)
        ttk.Spinbox(f, from_=1, to=5, width=8,
                    textvariable=self._var("stable_reads", "int")).grid(
            row=2, column=1, sticky="w", pady=6)
        ttk.Label(f, text="连续 N 次一致才翻译，防半句/淡入").grid(row=2, column=2, sticky="w")

        ttk.Label(f, text="OCR 放大倍数").grid(row=3, column=0, sticky="w", padx=(12, 8), pady=6)
        ttk.Spinbox(f, from_=1.0, to=4.0, increment=0.5, width=8,
                    textvariable=self._var("upscale", "dbl")).grid(
            row=3, column=1, sticky="w", pady=6)
        ttk.Label(f, text="小字号建议 2.0").grid(row=3, column=2, sticky="w")

        ttk.Checkbutton(f, text="OCR 前做 Otsu 二值化（花屏/低对比时开）",
                        variable=self._var("binarize", "bool")).grid(
            row=4, column=1, columnspan=2, sticky="w", pady=6)

        ttk.Label(f, text="日文占比阈值").grid(row=5, column=0, sticky="w", padx=(12, 8), pady=6)
        ttk.Spinbox(f, from_=0.0, to=1.0, increment=0.05, width=8,
                    textvariable=self._var("min_ratio", "dbl")).grid(
            row=5, column=1, sticky="w", pady=6)
        ttk.Label(f, text="低于此值当 UI 噪声丢弃").grid(row=5, column=2, sticky="w")

        ttk.Label(f, text="OCR 引擎").grid(row=6, column=0, sticky="w", padx=(12, 8), pady=6)
        eng = ttk.Combobox(f, state="readonly", width=24, values=["winocr", "tesseract"])
        eng.set(self.cfg.get("ocr_engine", "winocr"))
        self._ocr_cb = eng
        eng.grid(row=6, column=1, sticky="w", pady=6)
        ttk.Label(f, text="winocr = Windows 内置，免安装", foreground="#7a8494").grid(
            row=6, column=2, sticky="w")

        ttk.Label(f, text="当前区域").grid(row=7, column=0, sticky="w", padx=(12, 8), pady=6)
        self._lbl_region = ttk.Label(f, text=str(self.cfg.region))
        self._lbl_region.grid(row=7, column=1, sticky="w", pady=6)
        ttk.Label(f, text="主界面「框选区域」可重设").grid(row=7, column=2, sticky="w")

        ttk.Button(f, text="清空区域", command=self._clear_region).grid(
            row=8, column=1, sticky="w", pady=10)

        # ---- 识别目标程序：不用手框，直接选正在运行的游戏 ----
        box = ttk.LabelFrame(f, text="识别目标程序（选好后自动跟住整个游戏窗口）")
        box.grid(row=9, column=0, columnspan=3, sticky="we", padx=8, pady=(16, 10))
        box.columnconfigure(0, weight=1)
        self._targets: list[dict] = []
        self._target_cb = ttk.Combobox(box, state="readonly", width=52)
        self._target_cb.grid(row=0, column=0, sticky="we", padx=(10, 6), pady=6)
        ttk.Button(box, text="刷新列表", command=self._refresh_targets).grid(
            row=0, column=1, sticky="w", padx=(0, 10), pady=6)
        ttk.Label(
            box,
            text="选定后每轮按进程找窗口、取它的整个客户区来抓字：游戏移动、换分辨率、"
                 "重启都不用重新框选，中文覆盖层也会跟着嵌进这个窗口。\n"
                 "主界面按 F7 手框区域会清除这个设定。",
            foreground="#7a8494", wraplength=560, justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 8))
        self._refresh_targets()

    def _tab_display(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="显示")
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="中文字号").grid(row=0, column=0, sticky="w", padx=(12, 8), pady=6)
        ttk.Spinbox(f, from_=10, to=48, width=8,
                    textvariable=self._var("zh_size", "int")).grid(row=0, column=1, sticky="w")
        ttk.Label(f, text="日文字号").grid(row=1, column=0, sticky="w", padx=(12, 8), pady=6)
        ttk.Spinbox(f, from_=8, to=32, width=8,
                    textvariable=self._var("jp_size", "int")).grid(row=1, column=1, sticky="w")
        ttk.Label(f, text="语法字号").grid(row=2, column=0, sticky="w", padx=(12, 8), pady=6)
        ttk.Spinbox(f, from_=8, to=24, width=8,
                    textvariable=self._var("gram_size", "int")).grid(row=2, column=1, sticky="w")

        ttk.Label(f, text="不透明度").grid(row=3, column=0, sticky="w", padx=(12, 8), pady=6)
        ttk.Spinbox(f, from_=0.30, to=1.0, increment=0.05, width=8,
                    textvariable=self._var("overlay_alpha", "dbl")).grid(row=3, column=1, sticky="w")
        ttk.Checkbutton(f, text="背景透明（只留文字）", variable=self._var("overlay_bg_transparent", "bool")).grid(
            row=3, column=2, sticky="w", padx=(8, 0))
        ttk.Label(f, text="窗口宽度").grid(row=4, column=0, sticky="w", padx=(12, 8), pady=6)
        ttk.Spinbox(f, from_=380, to=2400, increment=20, width=8,
                    textvariable=self._var("overlay_width", "int")).grid(row=4, column=1, sticky="w")

        ttk.Label(f, text="主题").grid(row=5, column=0, sticky="w", padx=(12, 8), pady=6)
        th = ttk.Combobox(f, state="readonly", values=["dark", "light"], width=10)
        th.set(self.cfg.get("theme", "dark"))
        self._theme_cb = th
        th.grid(row=5, column=1, sticky="w")

        box = ttk.LabelFrame(f, text="显示哪些内容")
        box.grid(row=6, column=0, columnspan=3, sticky="ew", padx=12, pady=10)
        for i, (key, label) in enumerate([
            ("show_jp", "日文原句"), ("show_kana", "假名读音"),
            ("show_vocab", "重点词"), ("show_notes", "语气/语境提示"),
        ]):
            ttk.Checkbutton(box, text=label, variable=self._var(key, "bool")).grid(
                row=i // 2, column=i % 2, sticky="w", padx=10, pady=4)

        ttk.Label(f, text="日文字体").grid(row=7, column=0, sticky="w", padx=(12, 8), pady=6)
        ttk.Entry(f, textvariable=self._var("font_jp")).grid(row=7, column=1, sticky="ew")
        ttk.Label(f, text="中文字体").grid(row=8, column=0, sticky="w", padx=(12, 8), pady=6)
        ttk.Entry(f, textvariable=self._var("font_zh")).grid(row=8, column=1, sticky="ew")

        # 覆盖原文 + 录屏/NVIDIA 录制：这块决定了「录出来的视频里有没有中文」
        cap = ttk.LabelFrame(f, text="覆盖原文与录制")
        cap.grid(row=9, column=0, columnspan=3, sticky="ew", padx=12, pady=10)
        cap.columnconfigure(1, weight=1)
        ttk.Label(cap, text="截图可见性").grid(row=0, column=0, sticky="w", padx=(10, 8), pady=6)
        cap_cb = ttk.Combobox(
            cap, state="readonly", width=30,
            values=[
                "exclude（默认：录制里看不到中文）",
                "record（录制里能看到中文）",
                "off（永不对截屏隐藏）",
            ],
        )
        _cur = str(self.cfg.get("cover_capture", "exclude") or "exclude").lower()
        cap_cb.current({"exclude": 0, "record": 1, "off": 2}.get(_cur, 0))
        self._capture_cb = cap_cb
        cap_cb.grid(row=0, column=1, sticky="w")
        ttk.Label(cap, text="想让 NVIDIA / OBS 录到中文，选 record；只自己看游戏、要录干净画面则保持 exclude。",
                  wraplength=520, justify="left").grid(
            row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 8))
        ttk.Checkbutton(cap, text="启动时自动开启覆盖原文",
                        variable=self._var("cover_mode", "bool")).grid(
            row=2, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 8))
        ttk.Checkbutton(cap, text="内嵌到游戏窗口（中文永远在游戏上面，跟随游戏移动/最小化）",
                        variable=self._var("cover_embed", "bool")).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 4))
        ttk.Label(cap, text="内嵌后覆盖层属于游戏窗口：点游戏不会被盖住，拖走游戏中文跟着走，"
                            "游戏最小化时一起收起。独占全屏仍然无解（Windows 不允许任何窗口盖上去）。",
                  wraplength=520, justify="left").grid(
            row=4, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 8))

    def _tab_misc(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="缓存与热键")
        f.columnconfigure(1, weight=1)

        ttk.Checkbutton(f, text="启用台词缓存（同一句只翻译一次）",
                        variable=self._var("use_cache", "bool")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=12, pady=8)
        ttk.Label(f, text="缓存有效期(天)").grid(row=1, column=0, sticky="w", padx=(12, 8), pady=6)
        ttk.Spinbox(f, from_=0, to=3650, increment=30, width=8,
                    textvariable=self._var("cache_ttl_days", "int")).grid(
            row=1, column=1, sticky="w")
        ttk.Label(f, text="0 = 永久").grid(row=1, column=2, sticky="w")

        self.lbl_cache = ttk.Label(f, text=self._cache_text())
        self.lbl_cache.grid(row=2, column=1, sticky="w", pady=6)
        ttk.Button(f, text="清空缓存", command=self._clear_cache).grid(
            row=3, column=1, sticky="w", pady=4)

        ttk.Checkbutton(f, text="启用全局热键（F7 框选 / F8 自动 / F9 立即 / F10 显隐 / F6 穿透）",
                        variable=self._var("hotkeys", "bool")).grid(
            row=4, column=0, columnspan=3, sticky="w", padx=12, pady=10)
        ttk.Label(
            f,
            text=("热键在游戏全屏（窗口化无边框）下依然有效。\n"
                  "若与游戏冲突，关掉这里，用主界面按钮操作。"),
            foreground="#7a8494", justify="left",
        ).grid(row=5, column=0, columnspan=3, sticky="w", padx=12, pady=(0, 10))

    # ---------- 回调 ----------
    def _cache_text(self) -> str:
        if not self.store:
            return ""
        s = self.store.stats()
        return f"已缓存 {s['lines']} 句，复用 {s['replays']} 次"

    def _clear_cache(self):
        if self.store:
            self.store.clear()
            self.lbl_cache.configure(text=self._cache_text())

    def _clear_region(self):
        self.cfg.set("region", [0, 0, 0, 0])
        self._lbl_region.configure(text="[0, 0, 0, 0]")

    # ---------- 识别目标程序 ----------
    def _target_labels(self) -> list[str]:
        """下拉框文字。第 0 项永远是「不指定」，其余与 self._targets 一一对应。"""
        out = ["（不指定：用主界面框选的区域）"]
        for t in self._targets:
            w, h = (t.get("client") or (0, 0, 0, 0))[2:4]
            size = f"{w}x{h}" if not t.get("missing") else "未运行"
            out.append(f"{t.get('title') or '(无标题)'} · {t.get('exe') or '?'} · {size}")
        return out

    def _refresh_targets(self) -> None:
        """重新枚举「可以当识别目标」的窗口，并保住当前选择。"""
        keep = self._selected_target_pid()
        items: list[dict] = []
        try:
            items = winapi.list_targets(exclude_pids=[os.getpid()])
        except Exception:
            log.debug("枚举识别目标失败", exc_info=True)
        pid_saved = int(self.cfg.get("target_pid", 0) or 0)
        if pid_saved and not any(t["pid"] == pid_saved for t in items):
            # 上次选的程序现在没开着：留一条占位，别让用户以为设置丢了
            items.insert(0, {
                "pid": pid_saved,
                "exe": self.cfg.get("target_exe", ""),
                "title": self.cfg.get("target_title", ""),
                "client": (0, 0, 0, 0),
                "missing": True,
            })
        self._targets = items
        labels = self._target_labels()
        self._target_cb.configure(values=labels)
        idx = 0
        want = keep or pid_saved
        if want:
            for i, t in enumerate(self._targets, start=1):
                if int(t["pid"]) == int(want):
                    idx = i
                    break
        self._target_cb.current(idx)

    def _selected_target_pid(self) -> int:
        try:
            i = int(self._target_cb.current())
        except Exception:
            return 0
        if i <= 0 or i - 1 >= len(self._targets):
            return 0
        return int(self._targets[i - 1].get("pid") or 0)

    def _target_patch(self) -> dict:
        pid = self._selected_target_pid()
        if not pid:
            return {"target_pid": 0, "target_exe": "", "target_title": ""}
        t = next((x for x in self._targets if int(x.get("pid") or 0) == pid), {})
        return {
            "target_pid": pid,
            "target_exe": t.get("exe", "") or "",
            "target_title": t.get("title", "") or "",
        }

    def _on_provider(self, idx: int):
        key = self._provider_keys[idx]
        p = llm.PROVIDERS[key]
        self.vars["base_url"].set(p["base_url"])
        self.vars["model"].set(p["model"])
        self.vars["vision_model"].set(p.get("vision_model", ""))
        self._provider_current = key

    def _test(self):
        self.lbl_test.configure(text="测试中…", foreground="#7a8494")
        self.win.update_idletasks()
        try:
            from . import llm as llm_mod
            kw = {
                "base_url": self.vars["base_url"].get(),
                "model": self.vars["model"].get(),
                "api_key": self.vars["api_key"].get(),
                "temperature": 0.0,
                "max_tokens": 200,
                "timeout": min(60, int(self.vars["timeout"].get() or 30)),
                "extra_headers": self.cfg.get("extra_headers") or {},
            }
            res = llm_mod.analyze_text("そんなに慌てなくても、まだ間に合うはずだよ。", kw)
            self.lbl_test.configure(
                text=f"OK：{res.get('zh', '')[:28]}", foreground="#3a8f5c")
        except Exception as exc:
            self.lbl_test.configure(text=f"失败：{str(exc)[:60]}", foreground="#c0392b")

    def _reset(self):
        from .config import DEFAULTS

        for k, v in DEFAULTS.items():
            if k in self.vars:
                self.vars[k].set(v)
        self.e_gloss.delete("1.0", "end")
        for k, v in DEFAULTS.items():
            self.cfg.set(k, v)
        messagebox.showinfo("已重置", "字段已恢复默认，点「保存并应用」生效。")

    def _save(self, verify: bool = True):
        patch = {k: v.get() for k, v in self.vars.items()}
        patch["glossary"] = self.e_gloss.get("1.0", "end").strip()
        patch["provider"] = getattr(self, "_provider_current", self.cfg.get("provider"))
        patch["translate_engine"] = self.var_engine.get() or "llm"
        patch["mode"] = ["ocr", "clipboard", "vision"][self._mode_cb.current()] \
            if self._mode_cb.current() >= 0 else "ocr"
        patch["ocr_engine"] = self._ocr_cb.get() or "winocr"
        patch["theme"] = self._theme_cb.get() or "dark"
        if getattr(self, "_capture_cb", None) is not None:
            idx = self._capture_cb.current()
            patch["cover_capture"] = {0: "exclude", 1: "record", 2: "off"}.get(idx, "exclude")
        if getattr(self, "_target_cb", None) is not None:
            patch.update(self._target_patch())
        self.cfg.update(patch)
        self.cfg.save()
        if self.on_saved:
            self.on_saved()
        if not verify:
            self._close()
            return
        self._start_verify()

    # ---------- 保存后的配置校验 ----------
    def nb_select_engine(self) -> None:
        """把 Notebook 切到「翻译引擎」页（截图脚本和手测都用得上）。"""
        try:
            self._nb.select(self._tab_engine)
        except Exception:
            pass

    def _start_verify(self) -> None:
        """配置已落盘，再在后台真连一次接口；结果出来后弹窗，成功才关窗。

        放在线程里是因为：网络不好时一次请求能等到 20 秒，主线程卡住会让窗口
        「未响应」，用户以为程序死了。
        """
        for b in getattr(self, "_buttons", []):
            try:
                b.state(["disabled"])
            except Exception:
                pass
        try:
            self.lbl_verify.configure(text="正在校验配置…", foreground="#7a8494")
        except Exception:
            pass
        engine = self.var_engine.get() or "llm"
        self._verify_result = None
        self._verify_done = threading.Event()

        def work():
            try:
                res = self._verify(engine)
            except Exception as exc:                      # 校验自己崩了也要有交代
                res = ("error", "校验时出错", f"{type(exc).__name__}: {exc}"
                       "\n\n配置已经保存，可以先用着；方便的话把这行发给我。")
            self._verify_result = res
            self._verify_done.set()

        threading.Thread(target=work, daemon=True).start()
        # 结果由主线程轮询取回：Tk 的 after() 从子线程调用在没跑 mainloop 的场景
        # （比如回归脚本里手动 update()）会静默失效，弹窗就永远不出现。
        self._poll_verify()

    def _poll_verify(self) -> None:
        try:
            if not self.win.winfo_exists():
                return
        except tk.TclError:
            return
        if self._verify_done.is_set():
            level, title, body = self._verify_result or ("error", "校验未完成", "未知原因")
            self._finish_verify(level, title, body)
            return
        try:
            self.win.after(120, self._poll_verify)
        except tk.TclError:
            pass

    def _finish_verify(self, level: str, title: str, body: str) -> None:
        try:
            self.lbl_verify.configure(text="")
        except Exception:
            pass
        try:
            if level == "ok":
                messagebox.showinfo(title, body, parent=self.win)
            elif level == "warn":
                messagebox.showwarning(title, body, parent=self.win)
            else:
                messagebox.showerror(title, body, parent=self.win)
        finally:
            self._close()

    def _verify(self, engine: str) -> tuple[str, str, str]:
        """真连一次翻译接口，顺带检查几个明显不合理的配置项。

        返回 (level, 标题, 正文)，level ∈ {ok, warn, error}。纯函数、不碰界面，
        方便回归脚本直接调用。
        """
        notes: list[str] = []
        cfg = self.cfg

        # 明显不合理的数值：只提醒，不改用户的值
        try:
            poll = int(cfg.get("poll_ms", 250) or 0)
            if poll < 100:
                notes.append(f"自动翻译间隔 {poll}ms 偏小，识别会排队；建议 ≥150ms。")
            up = float(cfg.get("upscale", 1.6) or 0)
            if up < 1.0 or up > 3.0:
                notes.append(f"放大倍数 {up} 超出 1.0~3.0，OCR 容易失真。")
            mr = float(cfg.get("min_ratio", 0.3) or 0)
            if mr < 0.1 or mr > 0.9:
                notes.append(f"墨迹阈值 min_ratio={mr} 不在 0.1~0.9 之间。")
        except Exception:
            pass
        region = cfg.region
        if region[2] <= 0 or region[3] <= 0:
            notes.append("还没框选字幕区域：回到主窗口按 F7 框住对话框的文字那块。")
        if int(cfg.get("context_lines", 2) or 0) > 6:
            notes.append("上文条数超过 6 会让每句更慢、更贵。")

        tail = ("\n\n（以上选项都已经保存到 config.json，不影响你先用起来。）"
                if notes else "")
        note_block = ("\n\n另外：\n· " + "\n· ".join(notes)) if notes else ""

        if engine == "ms":
            key = (cfg.get("ms_key") or "").strip()
            if not key:
                msg = ("配置已保存。\n\n不过还没填「微软 Key」，现在只显示本地句型库的语法和生词，"
                       "没有中文译文。\n去 Azure 门户 → 翻译器资源 → 密钥和终结点，把 KEY 1 粘进去，"
                       "再点一次「保存并应用」就会自动校验。")
                return "warn", "已保存，还没配微软翻译的 Key", msg + note_block
            try:
                from . import msmt

                out = msmt.translate(
                    "そんなに慌てなくても、まだ間に合うはずだよ。",
                    key=key,
                    region=cfg.get("ms_region") or "",
                    endpoint=cfg.get("ms_endpoint") or msmt.DEFAULT_ENDPOINT,
                    timeout=min(20, int(cfg.get("timeout", 20) or 20)),
                )
            except Exception as exc:
                return ("error", "配置已保存，但微软翻译没连上",
                        f"{exc}{note_block}{tail}")
            body = (f"配置已保存，并且真的翻了一句：\n\n"
                    f"日文：そんなに慌てなくても、まだ間に合うはずだよ。\n"
                    f"中文：{out}\n\n"
                    f"引擎：微软翻译（区域：{cfg.get('ms_region') or '全球'}）\n"
                    f"语法与生词仍由本地句型库提供。")
            return "ok", "配置已保存并通过校验", body + note_block

        # 外部 API 模式
        provider = cfg.get("provider") or "deepseek"
        if not (cfg.get("model") or "").strip():
            return ("error", "配置已保存，但模型名是空的",
                    "「模型」页里的「文本模型」必须填，例如 deepseek-chat。" + note_block + tail)
        if not (cfg.get("base_url") or "").strip():
            return ("error", "配置已保存，但 base_url 是空的",
                    "「模型」页里的 base_url 要填到 /v1 为止，例如 https://api.deepseek.com/v1。"
                    + note_block + tail)
        if not cfg.has_key and provider != "ollama":
            return ("warn", "已保存，还没填 API Key",
                    "现在只显示本地句型库的语法和生词，没有中文译文。\n"
                    "到「模型」页填 API Key，再点一次「保存并应用」会自动校验；"
                    "本地 Ollama / LM Studio 可以不填。" + note_block)
        try:
            kwargs = {
                "base_url": cfg.get("base_url"),
                "model": cfg.get("model"),
                "api_key": cfg.get("api_key"),
                "temperature": 0.0,
                "max_tokens": 300,
                "timeout": min(25, int(cfg.get("timeout", 60) or 25)),
                "extra_headers": cfg.get("extra_headers") or {},
            }
            res = llm.analyze_text("そんなに慌てなくても、まだ間に合うはずだよ。", kwargs, context=[])
        except Exception as exc:
            return ("error", "配置已保存，但外部 API 没连上", f"{exc}{note_block}{tail}")
        zh = (res or {}).get("zh") or ""
        if not zh:
            return ("error", "配置已保存，但模型没有返回译文",
                    "接口通了，可是返回里没有中文。检查模型名是否支持对话补全，或换个模型试试。"
                    + note_block + tail)
        g = len((res or {}).get("grammar") or [])
        v = len((res or {}).get("vocab") or [])
        body = (f"配置已保存，并且真的翻了一句：\n\n"
                f"日文：そんなに慌てなくても、まだ間に合うはずだよ。\n"
                f"中文：{zh}\n\n"
                f"引擎：{cfg.get('model')} @ {cfg.get('base_url')}\n"
                f"同时返回：语法 {g} 条、重点词 {v} 个。")
        return "ok", "配置已保存并通过校验", body + note_block

    def _close(self) -> None:
        try:
            self.win.destroy()
        except tk.TclError:
            pass
