"""录制兼容模式回归：覆盖层对「截屏/录制」的可见性必须按模式切换，且不闪。

背景：1.0.9 起覆盖层用 `WDA_EXCLUDEFROMCAPTURE` 永久躲开截屏——代价是 NVIDIA/OBS
录出来的画面里**没有中文**（用户报「使用时无法使用英伟达录制」）。新增 record 模式：
平时可见，只在取词那一瞬排除，人眼看不出变化。

断言（用真实 Tk 窗口 + GetWindowDisplayAffinity 读回内核状态）：
1. exclude：常驻排除（0x11）。
2. record：静止时 0x0，dip(True) 时 0x11，dip(False) 后回到 0x0。
3. record 模式全程窗口保持可见（IsWindowVisible 一直为真）→ 没有隐藏/重绘，所以不闪。
4. off：始终 0x0，且 dip 走「隐身」老路径（withdraw/deiconify）。
5. 老配置 cover_exclude_capture=false 仍能映射成 off。

跑法：python capture_mode_check.py
"""

from __future__ import annotations

import ctypes
import sys
import time
import tkinter as tk

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, ".")

from galtl import winapi
from galtl.config import Config
from galtl.ui_cover import CoverWindow

user32 = ctypes.windll.user32

fails: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    if not ok:
        fails.append(f"{name}: got={got!r} want={want!r}")
    print(f"  [{'ok' if ok else 'FAIL'}] {name}: {got!r}")


def affinity(hwnd: int) -> int:
    flag = ctypes.c_ulong(0)
    ok = user32.GetWindowDisplayAffinity(ctypes.c_void_p(int(hwnd)), ctypes.byref(flag))
    return int(flag.value) if ok else -1


def visible(hwnd: int) -> bool:
    return bool(user32.IsWindowVisible(ctypes.c_void_p(int(hwnd))))


def make_cfg(capture: str | None, legacy: bool | None = None) -> Config:
    cfg = Config()
    cfg.data["cover_mode"] = True
    cfg.data["cover_match_bg"] = False          # 取样会开窗读像素，这里不需要
    if capture is not None:
        cfg.data["cover_capture"] = capture
    if legacy is not None:
        cfg.data["cover_exclude_capture"] = legacy
    return cfg


root = tk.Tk()
root.withdraw()

print("1) exclude 模式：常驻排除")
cov = CoverWindow(root, make_cfg("exclude"))
check("capture_mode", cov.capture_mode, "exclude")
check("静止时亲和", affinity(cov._hwnd), winapi.WDA_EXCLUDEFROMCAPTURE)
cov.show("测试")
root.update()
check("显示后仍排除", affinity(cov._hwnd), winapi.WDA_EXCLUDEFROMCAPTURE)
cov.destroy()

print("2) record 模式：只在取词瞬间排除")
cov = CoverWindow(root, make_cfg("record"))
check("capture_mode", cov.capture_mode, "record")
check("静止时亲和", affinity(cov._hwnd), winapi.WDA_NONE)
cov.set_rect((300, 300, 600, 120))
cov.show("录制里应该能看到这句中文")
root.update()
check("显示后仍不排除", affinity(cov._hwnd), winapi.WDA_NONE)
before = visible(cov._hwnd)
cov.dip(True)
root.update()
check("dip 时亲和", affinity(cov._hwnd), winapi.WDA_EXCLUDEFROMCAPTURE)
check("dip 时窗口仍可见（不闪）", visible(cov._hwnd), before)
cov.dip(False)
root.update()
check("dip 结束后恢复可见性", affinity(cov._hwnd), winapi.WDA_NONE)
check("dip 结束后窗口仍可见", visible(cov._hwnd), True)

print("3) record 模式反复 dip 不残留")
for _ in range(5):
    cov.dip(True)
    cov.dip(False)
root.update()
check("5 轮后亲和", affinity(cov._hwnd), winapi.WDA_NONE)
cov.destroy()

print("4) off 模式：永不排除，dip 走隐身老路径")
cov = CoverWindow(root, make_cfg("off"))
check("capture_mode", cov.capture_mode, "off")
check("静止时亲和", affinity(cov._hwnd), winapi.WDA_NONE)
cov.set_rect((300, 300, 600, 120))
cov.show("off 模式")
root.update()
check("显示后不排除", affinity(cov._hwnd), winapi.WDA_NONE)
cov.dip(True)
root.update()
check("dip 时窗口被藏起", visible(cov._hwnd), False)
cov.dip(False)
root.update()
check("dip 结束后窗口回来", visible(cov._hwnd), True)
cov.destroy()

print("5) 老配置兼容：cover_exclude_capture=false → off（走真实文件 + Config.load 迁移）")
import json
import tempfile
from pathlib import Path

_tmp = Path(tempfile.gettempdir()) / "galtl_legacy_cfg.json"
_tmp.write_text(json.dumps({"cover_exclude_capture": False, "overlay_width": 900}, ensure_ascii=False),
                encoding="utf-8")
_cfg = Config(str(_tmp))
_cfg.load()
check("迁移后的 cover_capture", _cfg.get("cover_capture"), "off")
cov = CoverWindow(root, _cfg)
check("capture_mode", cov.capture_mode, "off")
cov.destroy()
_tmp.unlink(missing_ok=True)

print("6) 运行时切换模式立即生效")
cov = CoverWindow(root, make_cfg("exclude"))
check("初始 exclude", affinity(cov._hwnd), winapi.WDA_EXCLUDEFROMCAPTURE)
check("切到 record", cov.set_capture_mode("record"), "record")
root.update()
time.sleep(0.05)
check("切换后亲和", affinity(cov._hwnd), winapi.WDA_NONE)
check("切回 exclude", cov.set_capture_mode("exclude"), "exclude")
time.sleep(0.05)
check("切换后亲和", affinity(cov._hwnd), winapi.WDA_EXCLUDEFROMCAPTURE)
cov.destroy()

try:
    root.destroy()
except tk.TclError:
    pass

print()
if fails:
    print(f"失败 {len(fails)} 项：")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("录制兼容模式回归：全部通过")
