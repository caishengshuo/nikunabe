"""设置面板（1.0.18 新版「覆盖原文与录制」）的布局回归 + 截图。

为什么要单独跑：`SettingsDialog` 是 transient 子窗口，只有父窗口真的可见时才会
被 Windows 映射出来；在无头/父窗口 withdraw 的情况下截出来只有父窗口那一小块。
所以这里用 `wm_frame()` 直接拿 Tk 的真实窗口句柄，再用 PrintWindow 抓（被遮挡也能抓）。

断言：
1. 「显示」页里存在「覆盖原文与录制」这块；
2. 下拉框三档齐全，且默认值与配置一致；
3. 这块的请求宽度不超过对话框宽度（不会横向截断）；
4. 整页内容高度放得下（不会纵向溢出）；
5. 改成 record 后保存，配置里写入 cover_capture=record。

跑法：python settings_layout_check.py [out.png]
"""

from __future__ import annotations

import ctypes
import sys
import time
import tkinter as tk
from tkinter import ttk

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, ".")

from galtl import winapi
from galtl.config import Config
from galtl.ui_settings import SettingsDialog

out = sys.argv[1] if len(sys.argv) > 1 else r"G:\日语\promo\settings_1_0_18.png"

fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if not ok:
        fails.append(f"{name} {detail}".strip())
    print(f"  [{'ok' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")


def shot(win: tk.Toplevel, path: str) -> tuple[int, int]:
    """PrintWindow 抓 Tk 真实窗口（被别的窗口盖住也能抓到）。"""
    win.update_idletasks()
    win.update()
    frame = win.wm_frame()
    hwnd = int(str(frame), 16)
    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                    ("r", ctypes.c_long), ("b", ctypes.c_long)]
    r = RECT()
    ctypes.windll.user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r))
    w, h = r.r - r.l, r.b - r.t
    png, pw, ph = winapi.grab_hwnd_png(hwnd)
    with open(path, "wb") as f:
        f.write(png)
    return pw, ph


root = tk.Tk()
root.geometry("160x100+40+40")
root.update()

cfg = Config()
cfg.data["cover_capture"] = "exclude"
dlg = SettingsDialog(root, cfg, on_saved=None)

nb = None
for w in dlg.win.winfo_children():
    if isinstance(w, ttk.Notebook):
        nb = w
        break
assert nb is not None, "找不到 Notebook"

idx = None
for i in range(nb.index("end")):
    if "显示" in nb.tab(i, "text"):
        idx = i
nb.select(idx)
dlg.win.deiconify()
root.update()
time.sleep(0.5)
dlg.win.update()

print("1) 「覆盖原文与录制」这块存在")
frames = []


def walk(w):
    for c in w.winfo_children():
        yield c
        yield from walk(c)


for w in walk(dlg.win):
    if isinstance(w, ttk.LabelFrame) and "覆盖" in str(w.cget("text")):
        frames.append(w)
check("LabelFrame 存在", len(frames) == 1, f"找到 {len(frames)} 个")
cap = frames[0] if frames else None

print("2) 下拉三档 + 默认值")
cb = getattr(dlg, "_capture_cb", None)
check("有 _capture_cb", cb is not None)
if cb is not None:
    vals = list(cb.cget("values"))
    check("三档齐全", len(vals) == 3, str(vals))
    check("默认选中 exclude", cb.current() == 0, f"current={cb.current()}")
    check("下拉当前值与配置一致",
          {"exclude": 0, "record": 1, "off": 2}[cfg.get("cover_capture")] == cb.current())

print("3) 横向不截断 / 4) 纵向放得下")
dlg.win.update_idletasks()
win_w = dlg.win.winfo_width()
win_h = dlg.win.winfo_height()
if cap is not None:
    need_w = cap.winfo_reqwidth()
    check("这块的请求宽度放得下", need_w <= win_w - 24, f"need={need_w} win={win_w}")
tab = nb.nametowidget(nb.select())
need_h = tab.winfo_reqheight()
check("整页请求高度放得下", need_h <= win_h - 40, f"need={need_h} win={win_h}")
check("对话框已真正映射", dlg.win.winfo_ismapped() == 1)

print("5) 改成 record 能落盘")
pw, ph = shot(dlg.win, out)
print("saved", out, f"{pw}x{ph}")

print("6) 新增的「翻译引擎」页")
eng_idx = None
for i in range(nb.index("end")):
    if "翻译引擎" in nb.tab(i, "text"):
        eng_idx = i
check("存在「翻译引擎」页", eng_idx is not None, f"tabs={[nb.tab(i, 'text') for i in range(nb.index('end'))]}")
if eng_idx is not None:
    nb.select(eng_idx)
    dlg.win.update()
    time.sleep(0.3)
    var_engine = getattr(dlg, "var_engine", None)
    check("有 var_engine", var_engine is not None)
    if var_engine is not None:
        want = str(cfg.get("translate_engine") or "llm")
        check("单选默认值与配置一致", var_engine.get() == want,
              f"{var_engine.get()} != {want}")
        print("   - 选 llm 时微软那几个框应当禁用")
        var_engine.set("llm")
        dlg._sync_engine_state()
        dlg.win.update()
        states = [str(w.cget("state")) for w in (dlg.ms_key, dlg.ms_region, dlg.ms_endpoint)]
        check("llm 模式下微软输入框禁用", all(s == "disabled" for s in states), str(states))
        print("   - 选 ms 时应当可用")
        var_engine.set("ms")
        dlg._sync_engine_state()
        dlg.win.update()
        states = [str(w.cget("state")) for w in (dlg.ms_key, dlg.ms_region, dlg.ms_endpoint)]
        check("ms 模式下微软输入框可用", all(s == "normal" for s in states), str(states))
        out2 = out.replace(".png", "_engine.png")
        pw2, ph2 = shot(dlg.win, out2)
        print("saved", out2, f"{pw2}x{ph2}")
        tab = nb.nametowidget(nb.select())
        check("引擎页宽度放得下", tab.winfo_reqwidth() <= dlg.win.winfo_width() - 24,
              f"need={tab.winfo_reqwidth()} win={dlg.win.winfo_width()}")
        check("引擎页高度放得下", tab.winfo_reqheight() <= dlg.win.winfo_height() - 40,
              f"need={tab.winfo_reqheight()} win={dlg.win.winfo_height()}")

print("7) 保存：录屏档 + 微软引擎同时落盘")
if cb is not None:
    cb.current(1)
try:
    dlg._save()          # 内部会 cfg.update + save + destroy
except tk.TclError:
    pass
if cb is not None:
    check("配置写入 cover_capture=record", cfg.get("cover_capture") == "record",
          cfg.get("cover_capture"))
if getattr(dlg, "var_engine", None) is not None:
    check("配置写入 translate_engine=ms", cfg.get("translate_engine") == "ms",
          str(cfg.get("translate_engine")))

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
print("设置面板布局：全部通过")
