"""配置读写。config.json 与程序同目录（不可写则回落 LOCALAPPDATA）。"""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

from .paths import CONFIG_PATH

log = logging.getLogger("galtl.config")

DEFAULTS: dict[str, Any] = {
    # ---- 抓取 ----
    "mode": "ocr",                 # ocr | clipboard | vision
    "region": [0, 0, 0, 0],        # 屏幕坐标 x, y, w, h
    "poll_ms": 250,                # 空闲轮询间隔（画面不动时只做廉价指纹比对）
    "use_probe": True,             # 先用 12% 缩略图指纹判断「画面变了没」，变了才 OCR
    "stable_reads": 2,             # 需要几次一致的画面才翻译（2 = 看一眼确认，防半帧闪现）
    "upscale": 2.0,                # OCR 前放大倍数
    "binarize": False,             # Otsu 二值化（花屏/低对比时可开）
    "min_ratio": 0.35,             # 日文字符占比阈值，低于此视作 UI 噪声
    # ---- 延迟（两段式）----
    "stream_translate": True,      # 译文边生成边显示（首字通常 0.3~0.8s 就出来）
    "grammar_phase": True,         # 译文先上屏，语法讲解第二段补上
    "grammar_async": True,         # 第二段放到后台线程，不堵下一句台词
    "translate_max_tokens": 240,
    "grammar_max_tokens": 520,
    # ---- OCR 引擎 ----
    "ocr_engine": "winocr",        # winocr | tesseract
    "ocr_lang": "ja",
    "tesseract_exe": "tesseract",
    "tesseract_lang": "jpn",
    # ---- 模型 ----
    "provider": "deepseek",
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-chat",
    "api_key": "",
    "vision_model": "",            # 走 vision 模式时用的多模态模型名
    "temperature": 0.3,
    "max_tokens": 1200,
    "timeout": 60,
    "extra_headers": {},           # 例如 {"HTTP-Referer": "..."}
    "glossary": "",                # 人名/术语表，逐行「日文=中文」
    "context_lines": 2,            # 带上文条数做上下文
    "auto_translate": False,
    # ---- 悬浮窗 ----
    "overlay_pos": [0, 0],
    "overlay_width": 1100,
    "overlay_alpha": 0.93,
    "jp_size": 12,
    "zh_size": 19,
    "gram_size": 11,
    "show_jp": True,
    "show_kana": False,
    "show_vocab": True,
    "show_notes": True,
    "overlay_locked": False,       # True = 鼠标穿透（不挡点击）
    "overlay_visible": True,
    # ---- 覆盖原文（把中文直接盖在游戏原文上）----
    "cover_mode": False,           # 按钮/F11 开关，开启后译文盖住原日文
    "cover_alpha": 1.0,
    "cover_match_bg": True,   # 取自游戏对话框底色，盖上去没有色差
    "cover_bg": "#0b0f16",
    "cover_fg": "#ffffff",
    "cover_max_size": 30,
    "cover_min_size": 13,
    "cover_exclude_capture": True,  # 让自己不被截图拍到（零闪烁的关键）
    "font_jp": "Yu Gothic UI",
    "font_zh": "Microsoft YaHei UI",
    "theme": "dark",               # dark | light
    # ---- 缓存 ----
    "use_cache": True,
    "cache_ttl_days": 0,           # 0 = 永久
    # ---- 热键 ----
    "hotkeys": True,
}


def _merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, path=CONFIG_PATH):
        self.path = Path(path)  # 允许传 str：CI/脚本里写 Config("x.json") 不该炸
        self.data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self._original: dict[str, Any] = copy.deepcopy(self.data)  # 载入时的快照，用于三方合并
        self._mtime: float = 0.0
        self.load()

    def _disk_mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0

    def load(self) -> None:
        self._original = copy.deepcopy(self.data)
        self._mtime = self._disk_mtime()
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
                self.data = _merge(DEFAULTS, raw)
                self._original = copy.deepcopy(self.data)
                self._mtime = self._disk_mtime()
        except Exception as exc:  # 配置损坏不该阻止启动
            log.warning("配置读取失败，使用默认值：%s", exc)

    def save(self) -> None:
        """写盘前先合并磁盘上新改的键，避免把手动编辑（比如填 api_key）冲掉。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = copy.deepcopy(self.data)
            if self._disk_mtime() > self._mtime:
                try:
                    disk = json.loads(self.path.read_text(encoding="utf-8-sig"))
                except Exception:
                    disk = {}
                if isinstance(disk, dict):
                    for k, v in disk.items():
                        if k not in self._original or self.data.get(k) == self._original.get(k):
                            data[k] = v  # 内存里没动过的键，以磁盘为准
            self.path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.data = data
            self._original = copy.deepcopy(data)
            self._mtime = self._disk_mtime()
        except Exception as exc:
            log.warning("配置写入失败：%s", exc)

    def get(self, key: str, default=None):
        return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value) -> None:
        self.data[key] = value

    def update(self, patch: dict) -> None:
        self.data.update(patch)

    # 便捷属性
    @property
    def region(self) -> tuple[int, int, int, int]:
        r = self.data.get("region") or [0, 0, 0, 0]
        try:
            return int(r[0]), int(r[1]), int(r[2]), int(r[3])
        except Exception:
            return 0, 0, 0, 0

    @property
    def has_key(self) -> bool:
        return bool((self.data.get("api_key") or "").strip())

    def llm_kwargs(self) -> dict:
        return {
            "base_url": self.data.get("base_url") or "",
            "model": self.data.get("model") or "",
            "api_key": self.data.get("api_key") or "",
            "temperature": float(self.data.get("temperature", 0.3)),
            "max_tokens": int(self.data.get("max_tokens", 1200)),
            "timeout": int(self.data.get("timeout", 60)),
            "extra_headers": self.data.get("extra_headers") or {},
        }
