"""悬浮字幕窗「拖不动」回归测试。

复现路径：拖动 → 立刻来一帧翻译结果（触发 _fit_height）→ 窗口是否被拉回原位。
    python overlay_drag_check.py
"""
from __future__ import annotations

import ctypes
import types

import tkinter as tk

from galtl import winapi
from galtl.config import Config
from galtl.ui_overlay import Overlay


def win_rect(hwnd: int) -> tuple[int, int, int, int]:
    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    r = RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def main() -> int:
    winapi.set_dpi_aware()
    cfg = Config()
    backup_pos = list(cfg.get("overlay_pos") or [0, 0])

    root = tk.Tk()
    root.withdraw()
    ov = Overlay(root, cfg)
    ov.win.deiconify()
    root.update()
    root.update_idletasks()

    hwnd = ov._hwnd_get()
    x0, y0, w0, h0 = win_rect(hwnd)
    print(f"初始位置 {x0},{y0}  尺寸 {w0}x{h0}  overlay_pos={cfg.get('overlay_pos')}")

    # 1) 按下 + 拖动 +120,+90
    press = types.SimpleNamespace(x_root=x0 + 20, y_root=y0 + 8, widget=ov.text)
    ov._on_press(press)
    root.update()
    move = types.SimpleNamespace(x_root=x0 + 140, y_root=y0 + 98, widget=ov.text)
    ov._on_drag(move)
    root.update()
    ax, ay, _, _ = win_rect(hwnd)
    print(f"拖动中      {ax},{ay}  期望 {x0 + 120},{y0 + 90}  -> {'OK' if (ax, ay) == (x0 + 120, y0 + 90) else 'FAIL'}")

    # 2) 拖动过程中来一帧结果（之前就是这里把窗口拉回原位的）
    ov.show({"jp": "こんな時間まで起きているなんて", "zh": "居然熬到这么晚",
                    "grammar": [{"pattern": "〜なんて", "level": "N3", "meaning": "竟然"}],
                    "vocab": [{"word": "心配", "reading": "しんぱい", "meaning": "担心"}],
                    "notes": "带责备的关心", "meta": {"ms": 1}})
    root.update()
    bx, by, bw, bh = win_rect(hwnd)
    _vx, _vy, _sw, _sh = winapi.virtual_screen()
    pinned = abs((by + bh) - (_vy + _sh - 4)) <= 3      # 内容太长时允许上移贴底
    ok2 = (bx, by) == (x0 + 120, y0 + 90) or pinned
    print(f"渲染一帧后  {bx},{by}  尺寸 {bw}x{bh}  -> {'OK' if ok2 else 'FAIL(被拉回)'}"
          f"{'（过高，已贴底）' if pinned and (bx, by) != (x0 + 120, y0 + 90) else ''}")

    # 3) 松手 → 位置是否落盘
    ov._on_release(types.SimpleNamespace(x_root=x0 + 140, y_root=y0 + 98))
    root.update()
    saved = list(cfg.get("overlay_pos") or [0, 0])
    print(f"松手后落盘  overlay_pos={saved}  期望 [{x0 + 120},{y0 + 90}]  -> "
          f"{'OK' if saved == [x0 + 120, y0 + 90] else 'FAIL'}")

    # 4) 重启（重新建一个 Overlay 读配置）是否在保存的位置
    ov.win.destroy()
    ov2 = Overlay(root, cfg)
    ov2.win.deiconify()
    root.update()
    cx, cy, cw, ch = win_rect(ov2._hwnd_get())
    # 字体大到悬浮窗本身就很高时，_place() 会把窗口贴回屏幕内（y 变小或顶到「至少留 40px」），
    # 所以只要求：x 精确、y 不超过落盘值、也不跑到贴底之外。
    _vx, _vy, sw, sh = winapi.virtual_screen()
    exp_lo = min(y0 + 90, sh - ch - 4)
    ok4 = cx == x0 + 120 and exp_lo - 3 <= cy <= y0 + 90
    print(f"重开后位置  {cx},{cy}（高{ch}）  期望 x={x0 + 120} 且 y∈[{exp_lo},{y0 + 90}]  -> "
          f"{'OK' if ok4 else 'FAIL'}")

    # 5) 位置重置回底部居中
    ov2.reset_pos()
    root.update()
    dx_, dy_, dw, dh = win_rect(ov2._hwnd_get())
    _vx, _vy, sw, sh = winapi.virtual_screen()
    exp_x = max(0, _vx + (sw - dw) // 2)
    print(f"重置后位置  {dx_},{dy_}  期望 x={exp_x}  -> {'OK' if dx_ == exp_x else 'FAIL'}")

    ov2.win.destroy()
    cfg.set("overlay_pos", backup_pos)
    cfg.save()
    root.destroy()
    print("恢复原配置：overlay_pos =", backup_pos)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

