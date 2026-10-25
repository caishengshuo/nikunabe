"""设置窗口：模型 / 抓取 / 显示 / 缓存与热键。"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from . import llm


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
        nb = ttk.Notebook(self.win)
        nb.pack(fill="both", expand=True, padx=10, pady=10)
        self._tab_model(nb)
        self._tab_capture(nb)
        self._tab_display(nb)
        self._tab_misc(nb)

        bar = ttk.Frame(self.win)
        bar.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(bar, text="恢复默认", command=self._reset).pack(side="left")
        ttk.Button(bar, text="取消", command=self.win.destroy).pack(side="right")
        ttk.Button(bar, text="保存并应用", command=self._save).pack(side="right", padx=8)

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

    def _save(self):
        patch = {k: v.get() for k, v in self.vars.items()}
        patch["glossary"] = self.e_gloss.get("1.0", "end").strip()
        patch["provider"] = getattr(self, "_provider_current", self.cfg.get("provider"))
        patch["mode"] = ["ocr", "clipboard", "vision"][self._mode_cb.current()] \
            if self._mode_cb.current() >= 0 else "ocr"
        patch["ocr_engine"] = self._ocr_cb.get() or "winocr"
        patch["theme"] = self._theme_cb.get() or "dark"
        if getattr(self, "_capture_cb", None) is not None:
            idx = self._capture_cb.current()
            patch["cover_capture"] = {0: "exclude", 1: "record", 2: "off"}.get(idx, "exclude")
        self.cfg.update(patch)
        self.cfg.save()
        if self.on_saved:
            self.on_saved()
        self.win.destroy()
