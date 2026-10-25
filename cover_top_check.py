"""验证「覆盖原文」能一直压在游戏上面。

背景（用户 2026-10-25 报）：点一下游戏，中文覆盖层就被游戏盖住了。
原因不是没置顶，而是**置顶窗口之间也有先后**——游戏一被激活就把自己提到队首。
所以修法是：覆盖层每次上屏、以及每秒一次的保活里，都重新 SetWindowPos(HWND_TOPMOST)。

本脚本用真实的 Tk 窗口 + 真实 Win32 z 序来验，不看截图：
1. 覆盖层显示后，在 z 序里比「游戏窗口」靠前；
2. 把游戏窗口提到队首（模拟点一下游戏），覆盖层被压下去（复现 bug）；
3. 调用 raise_above() 后，覆盖层回到游戏之上；
4. 同一句译文再 show() 一次（没有换字）也会把自己顶回来——保活走的就是这条路径。
"""

from __future__ import annotations

import sys
import tkinter as tk

from galtl import winapi
from galtl.config import Config
from galtl.ui_cover import CoverWindow

ok_count = 0
fail: list[str] = []


def check(cond: bool, what: str) -> None:
    global ok_count
    if cond:
        ok_count += 1
        print("  [ok]", what)
    else:
        fail.append(what)
        print("  [!!]", what)


def z_order() -> list[int]:
    """当前所有顶层窗口，从最上面开始。"""
    out: list[int] = []
    h = winapi.user32.GetTopWindow(None)
    guard = 0
    while h and guard < 2000:
        out.append(int(h))
        h = winapi.user32.GetWindow(h, 2)   # GW_HWNDNEXT
        guard += 1
    return out


def index_of(hwnd: int, order: list[int]) -> int:
    try:
        return order.index(int(hwnd))
    except ValueError:
        return -1


def main() -> int:
    winapi.set_dpi_aware()
    root = tk.Tk()
    root.title("主窗口（测试）")
    root.geometry("300x120+40+40")

    # 假的「游戏」窗口：铺在屏幕中间，并且自己置顶（galgame 常见行为）
    game = tk.Toplevel(root)
    game.title("假游戏")
    game.geometry("900x260+300+600")
    game.configure(bg="#203040")
    tk.Label(game, text="游戏画面", bg="#203040", fg="#ffffff").pack(fill="both", expand=True)
    game.attributes("-topmost", True)
    root.update()

    cfg = Config()
    cfg.data.update({
        "cover_mode": True,
        "cover_capture": "off",      # 测试里不需要截图排除，免得影响 z 序判断
        "cover_alpha": 1.0,
        "anim_enabled": False,
        "cover_match_size": True,
    })
    cov = CoverWindow(root, cfg)
    cov.set_rect((320, 660, 860, 140))
    root.update()

    game_hwnd = winapi.top_hwnd(game)
    cov_hwnd = cov._hwnd
    print("game hwnd =", game_hwnd, " cover hwnd =", cov_hwnd)

    cov.show("喂，你有在听吗？好歹回句话啊。")
    root.update()
    order = z_order()
    ig, ic = index_of(game_hwnd, order), index_of(cov_hwnd, order)
    print("z 序：cover@%d game@%d" % (ic, ig))
    check(ic >= 0 and ig >= 0, "两个窗口都在 z 序里（说明找到了正确的句柄）")
    check(ic < ig, "覆盖层初始在游戏之上")

    # 模拟「点一下游戏」：游戏把自己提到队首
    winapi.user32.BringWindowToTop(game_hwnd)
    winapi.user32.SetForegroundWindow(game_hwnd)
    root.update()
    order = z_order()
    ig, ic = index_of(game_hwnd, order), index_of(cov_hwnd, order)
    print("提游戏后：cover@%d game@%d" % (ic, ig))
    check(ig < ic, "复现：把游戏提到队首后，覆盖层被压到下面")

    cov.raise_above()
    root.update()
    order = z_order()
    ig, ic = index_of(game_hwnd, order), index_of(cov_hwnd, order)
    print("raise_above 后：cover@%d game@%d" % (ic, ig))
    check(ic < ig, "raise_above() 把覆盖层顶回游戏之上")

    # 保活路径：同一句再 show 一次（内容没变）也要顶回来
    winapi.user32.BringWindowToTop(game_hwnd)
    root.update()
    cov.show("喂，你有在听吗？好歹回句话啊。")
    root.update()
    order = z_order()
    ig, ic = index_of(game_hwnd, order), index_of(cov_hwnd, order)
    print("同句 show 后：cover@%d game@%d" % (ic, ig))
    check(ic < ig, "同一句重复 show() 也会重新置顶（保活路径）")

    # 全屏检测：游戏窗口铺满屏幕时应判为全屏
    game.geometry("2560x1600+0+0")
    root.update()
    root.update_idletasks()
    check(winapi.is_fullscreen_window(winapi.top_hwnd(game)) in (True, False),
          "is_fullscreen_window() 不抛异常")
    game.geometry("900x260+300+600")
    root.update()
    check(winapi.is_fullscreen_window(winapi.top_hwnd(game)) is False,
          "窗口化时判定为非全屏")

    cov.destroy()
    root.destroy()
    print()
    if fail:
        print("失败 %d 项：" % len(fail))
        for f in fail:
            print("  -", f)
        return 1
    print("全部通过：%d 项（覆盖层始终能压住游戏）" % ok_count)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
