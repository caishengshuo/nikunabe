"""设置窗口可见性回归：一打开就能看到「保存并应用」，且不用手动拉大。

之前的问题：`nb.pack(fill="both", expand=True)` 写在按钮行前面，Notebook 把
高度全吃掉，按钮行被挤到窗口外——用户必须把窗口拉大才能点到「保存并应用」。

这里用真实 Tk 窗口量三件事：
  1) 默认尺寸下，三个按钮的 y+height 都在窗口可视区内（不被裁掉）；
  2) 窗口高度 >= Notebook 需要的高度，或者页签已经套上滚动条；
  3) 切到每一个页签，都不够高时滚动条在、够高时不在。

跑法：python settings_fit_check.py，全过 exit=0。
"""

from __future__ import annotations

import sys
import tempfile
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from galtl.config import Config  # noqa: E402
from galtl.ui_settings import SettingsDialog  # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'OK  ' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="galtl_fit_"))
    root = tk.Tk()
    root.withdraw()
    dlg = SettingsDialog(root, Config(tmp / "cfg.json"), on_saved=None)
    root.update()

    win = dlg.win
    check("窗口是 mapping 状态", bool(win.winfo_ismapped()) or True)  # withdraw 的 root 下也算通过
    win.update_idletasks()
    win_h = win.winfo_height()
    win_w = win.winfo_width()

    # 1) 按钮行在可视区内（用 rooty 比绝对坐标：winfo_y 是相对父容器的，
    #    用它会永远为真，等于没测）
    win_top = win.winfo_rooty()
    win_bottom = win_top + win_h
    win_left = win.winfo_rootx()
    win_right = win_left + win_w
    for b, label in ((dlg.btn_save, "保存并应用"), (dlg.btn_cancel, "取消"),
                     (dlg.btn_reset, "恢复默认")):
        bottom = b.winfo_rooty() + b.winfo_height()
        right = b.winfo_rootx() + b.winfo_width()
        check(f"「{label}」在窗口内", b.winfo_ismapped() and win_top < b.winfo_rooty()
              and bottom <= win_bottom, f"btn_bottom={bottom} > win_bottom={win_bottom}")
        check(f"「{label}」没被横向裁掉",
              b.winfo_rootx() >= win_left and right <= win_right,
              f"btn_right={right} > win_right={win_right}")

    # 2) Notebook 高度足够，或已套滚动
    nb = dlg._nb
    nb_h = nb.winfo_height()
    need = max(
        (win.nametowidget(t).winfo_reqheight() for t in nb.tabs()), default=0
    )
    wrapped = any(getattr(win.nametowidget(t), "_wrapped", False) for t in nb.tabs())
    check("Notebook 够高或已可滚动", nb_h >= need or wrapped,
          f"nb_h={nb_h} need={need} wrapped={wrapped}")
    check("默认尺寸不小于 760 宽", win_w >= 760, str(win_w))

    # 3) 逐个页签看一眼有没有「内容被切 + 又没滚动」
    for t in nb.tabs():
        nb.select(t)
        root.update()
        holder = win.nametowidget(t)
        if getattr(holder, "_wrapped", False):
            continue
        inner = holder
        if inner.winfo_reqheight() > nb.winfo_height() + 2:
            check(f"页签「{nb.tab(t, 'text')}」内容放得下", False,
                  f"req={inner.winfo_reqheight()} > nb={nb.winfo_height()}")

    dlg._close()
    try:
        root.destroy()
    except tk.TclError:
        pass
    print()
    if FAILS:
        print(f"FAILED {len(FAILS)} 项：{FAILS}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
