"""把 assets/app.ico 套到 Tk 窗口上（标题栏 / 任务栏 / Alt-Tab 都用这一张）。

源码运行时图标在项目 assets/ 下；打包成单文件 exe 后在解包目录 _MEIPASS/assets/ 下。
Windows 上给 root 设 `iconbitmap(default=...)` 之后，新建的 Toplevel 会自动继承。
"""
from __future__ import annotations

import pathlib
import sys

_CACHE: pathlib.Path | None = None


def icon_path() -> pathlib.Path | None:
    global _CACHE
    if _CACHE is None:
        base = (
            pathlib.Path(getattr(sys, "_MEIPASS"))
            if getattr(sys, "frozen", False)
            else pathlib.Path(__file__).resolve().parent.parent
        )
        _CACHE = base / "assets" / "app.ico"
    return _CACHE if _CACHE.exists() else None


def apply(win) -> bool:
    """给一个 Tk/Toplevel 设置图标；失败不抛（图标只是观感，不该拖垮界面）。"""
    p = icon_path()
    if p is None or win is None:
        return False
    try:
        win.iconbitmap(default=str(p))   # 之后新建的 Toplevel 自动继承
        win.iconbitmap(str(p))
        return True
    except Exception:
        return False
