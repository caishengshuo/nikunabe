"""回归：设置里「选正在运行的程序」当识别目标。

测的是这几件事（都是用户会直接遇到的）：
  1. 枚举能把另一个进程的游戏窗口列出来（pid / exe / 标题 / 客户区）；
  2. 选定后目标窗口的客户区就是抓取区域（不再用手框的 region）；
  3. 窗口移动 / 改大小，抓取区域跟着变（不用重新框选）；
  4. 目标进程关掉后退回手框的 region，不会一直报错；
  5. 覆盖层认宿主时优先认这个进程（而不是靠几何猜）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import tkinter as tk

sys.path.insert(0, r"G:\日语")

from galtl import winapi                      # noqa: E402
from galtl.config import Config               # noqa: E402
from galtl.pipeline import Engine             # noqa: E402
from galtl.store import Store                 # noqa: E402
from galtl.ui_cover import CoverWindow        # noqa: E402

PY = r"C:\Python314\pythonw.exe"
FAKE = r"G:\日语\fake_game.py"
TITLE = "假游戏"
FAKE_GEOM = "900x260+240+420"

fails: list[str] = []
oks = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global oks
    if cond:
        oks += 1
        print(f"  [ok] {name}" + (f"  {detail}" if detail else ""))
    else:
        fails.append(name)
        print(f"  [FAIL] {name}  {detail}")


def find_fake(title_part: str, timeout: float = 15.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        for t in winapi.list_targets(min_w=200, min_h=120):
            if title_part in (t.get("title") or ""):
                return t
        time.sleep(0.2)
    return {}


def main() -> int:
    print("== 启动假游戏（独立进程）==")
    script = FAKE + f" --geometry {FAKE_GEOM}"
    proc = subprocess.Popen([PY, FAKE], cwd=r"G:\日语")
    try:
        target = find_fake(TITLE)
        if not target:
            print("！！ 没找到假游戏窗口，后续检查无法进行")
            return 1
        pid = int(target["pid"])
        hwnd = int(target["hwnd"])
        print(f"  目标 pid={pid} hwnd={hwnd} title={target['title']!r} "
              f"client={target['client']} exe={target['exe']}")

        print("== 1. 枚举内容 ==")
        check("列出了目标进程", bool(pid))
        check("exe 是 python 系", target["exe"].lower().startswith("python"), target["exe"])
        check("客户区尺寸合理", target["client"][2] > 200 and target["client"][3] > 120,
              str(target["client"]))
        mine = winapi.list_targets(exclude_pids=[os.getpid()])
        check("排除自己后不含本进程",
              all(t["pid"] != os.getpid() for t in mine))

        print("== 2/3. 引擎按目标窗口取区域 ==")
        cfg = Config()
        cfg.data["region"] = [10, 10, 111, 66]      # 故意给一个假的手框区域
        cfg.data["target_pid"] = pid
        cfg.data["target_exe"] = target["exe"]
        cfg.data["target_title"] = target["title"]
        cfg.data["target_inset"] = 0
        store = Store(r"G:\日语\cache_target_check.db")
        eng = Engine(cfg, store, __import__("queue").Queue())

        rect = eng.target_rect()
        check("target_hwnd 命中目标", eng.target_hwnd() == hwnd, str(eng.target_hwnd()))
        check("抓取区=目标客户区", rect == tuple(target["client"]), f"{rect} vs {target['client']}")

        # 移动窗口：目标矩形应跟着变
        user32 = winapi.user32
        user32.MoveWindow(winapi.ctypes.c_void_p(hwnd), 320, 500,
                          target["client"][2], target["client"][3], True)
        time.sleep(0.5)
        moved = winapi.client_rect(hwnd)
        check("移动后跟随", eng.target_rect() == tuple(moved),
              f"{eng.target_rect()} vs {moved}")

        # 改大小：抓取区宽度也要变
        user32.MoveWindow(winapi.ctypes.c_void_p(hwnd), 320, 500, 760, 300, True)
        time.sleep(0.5)
        resized = winapi.client_rect(hwnd)
        check("缩放后跟随", eng.target_rect() == tuple(resized),
              f"{eng.target_rect()} vs {resized}")

        print("== 4. 目标窗口不在了要能退回手框区域 ==")
        user32.MoveWindow(winapi.ctypes.c_void_p(hwnd), 320, 500, 760, 300, True)
        cfg.data["target_pid"] = 999999            # 不存在的进程
        check("无效 pid 返回 0", eng.target_hwnd() == 0)
        check("退回 region", eng.target_rect() == (10, 10, 111, 66), str(eng.target_rect()))
        cfg.data["target_pid"] = pid

        print("== 5. 覆盖层认宿主优先认目标进程 ==")
        root = tk.Tk()
        root.withdraw()
        cfg.data["cover_embed"] = True
        cfg.data["region"] = [5, 5, 300, 120]      # 故意放到一片空桌面区域
        cover = CoverWindow(root, cfg)
        cover.set_rect(eng.target_rect())
        picked = cover.detect_game()
        check("宿主=目标窗口", picked == hwnd, f"{picked} vs {hwnd}")
        bound = cover.bind_game(picked)
        check("bind 成功", bool(bound) and winapi.owner_of(cover._hwnd) == hwnd,
              f"owner={winapi.owner_of(cover._hwnd)}")
        cover.destroy()
        root.destroy()

        print("== 6. 设置面板的下拉框与写回 ==")
        from galtl.ui_settings import SettingsDialog
        root2 = tk.Tk()
        root2.withdraw()
        dlg = SettingsDialog(root2, cfg)
        dlg._refresh_targets()
        labels = list(dlg._target_cb.cget("values"))
        check("下拉框含目标程序", any(TITLE in s for s in labels), str(labels[:4]))
        check("首项是不指定", labels and "不指定" in str(labels[0]), str(labels[:1]))
        idx = next((i for i, s in enumerate(labels) if TITLE in s), -1)
        dlg._target_cb.current(idx)
        patch = dlg._target_patch()
        check("选中后 patch 带 pid", patch.get("target_pid") == pid, str(patch))
        check("patch 带 exe", bool(patch.get("target_exe")), str(patch.get("target_exe")))
        dlg._target_cb.current(0)
        check("选「不指定」写回 0", dlg._target_patch().get("target_pid") == 0)
        dlg._close()
        root2.destroy()
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass
        try:
            os.remove(r"G:\日语\cache_target_check.db")
        except OSError:
            pass

    print(f"\n== 结果：{oks} 项通过，{len(fails)} 项失败 ==")
    for f in fails:
        print("  失败：", f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
