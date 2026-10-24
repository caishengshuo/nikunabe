"""路径与运行目录。配置/缓存写应用目录，不可写时回落到 %LOCALAPPDATA%\\galtl。"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time


def _app_dir() -> pathlib.Path:
    if getattr(sys, "frozen", False):
        return pathlib.Path(sys.executable).resolve().parent
    return pathlib.Path(__file__).resolve().parent.parent


def _pkg_dir() -> pathlib.Path:
    """程序自带只读资源（ps/ocr_worker.ps1）所在目录。

    PyInstaller onefile 运行时资源被解到 sys._MEIPASS，可写数据仍跟着 exe 走。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return pathlib.Path(meipass) / "galtl"
    return pathlib.Path(__file__).resolve().parent


APP_DIR = _app_dir()


def _data_dir() -> pathlib.Path:
    cand = [APP_DIR, pathlib.Path(os.environ.get("LOCALAPPDATA", str(pathlib.Path.home()))) / "galtl"]
    for c in cand:
        try:
            c.mkdir(parents=True, exist_ok=True)
            probe = c / ".write_probe"
            probe.write_text("1", encoding="utf-8")
            probe.unlink()
            return c
        except Exception:
            continue
    return pathlib.Path(tempfile.gettempdir()) / "galtl"


DATA_DIR = _data_dir()
CONFIG_PATH = DATA_DIR / "config.json"
DB_PATH = DATA_DIR / "cache.db"
LOG_PATH = DATA_DIR / "galtl.log"

# OCR 中间图片必须落在 ASCII 路径上（PowerShell 传参安全）
WORK_DIR = pathlib.Path(tempfile.gettempdir()) / "galtl_shots"
PKG_DIR = _pkg_dir()
OCR_PS1 = PKG_DIR / "ps" / "ocr_worker.ps1"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    _sweep_work_dir()


def _sweep_work_dir(max_age_hours: float = 6.0) -> None:
    """清掉旧的 OCR 中间图，避免临时目录里堆垃圾（也顺手解掉残留句柄留下的文件）。"""
    try:
        cutoff = time.time() - max_age_hours * 3600.0
        for p in WORK_DIR.glob("s*.png"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except Exception:
                continue
    except Exception:
        pass
