"""内嵌覆盖层回归：owned window 的 z 序、跟随、裁剪、最小化、截屏排除。

假游戏在**另一个进程**里跑（真实情形：游戏不是我们自己），
这样才能验证 detect_game() 的 pid 过滤与 owner 绑定。

    C:\\Python314\\pythonw.exe embed_check.py     （结果看 embed_check.log 与退出码）
"""
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time
import tkinter as tk

sys.path.insert(0, r"G:\日语")
from galtl import winapi                      # noqa: E402
from galtl.ui_cover import CoverWindow        # noqa: E402

LOG = open(r"G:\日语\embed_check.log", "w", encoding="utf-8")
FAILS: list[str] = []
user32 = ctypes.windll.user32
HWND_TOPMOST = ctypes.c_void_p(-1 & 0xFFFFFFFFFFFFFFFF)
SW_MINIMIZE = 6
SW_RESTORE = 9


def log(*a):
    print(*a, file=LOG, flush=True)


def check(name, ok, detail=""):
    log(("PASS  " if ok else "FAIL  ") + name + (("  <- " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)


class FakeCfg:
    def __init__(self, **kw):
        self.data = dict(kw)

    def get(self, k, d=None):
        return self.data.get(k, d)

    def set(self, k, v):
        self.data[k] = v

    def save(self):
        pass


def z_order():
    """当前所有顶层窗口的 z 序（靠前 = 更上面）。"""
    order = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _lp):
        order.append(int(hwnd))
        return True

    user32.EnumWindows(cb, 0)
    return order


def above(hwnd, other):
    """hwnd 是否排在 other 之前（= 显示在它上面）。"""
    order = z_order()
    try:
        return order.index(int(hwnd)) < order.index(int(other))
    except ValueError:
        return False


def windows_of(pid):
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _lp):
        p = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(hwnd):
            out.append(int(hwnd))
        return True

    user32.EnumWindows(cb, 0)
    return out


def magenta_ratio(rect):
    x, y, w, h = rect
    buf, _bw, _bh = winapi.capture_bgra(x, y, w, h, layered=True)
    if not buf:
        return -1.0
    hit = tot = 0
    for i in range(0, len(buf) - 4, 16):
        b, g, r = buf[i], buf[i + 1], buf[i + 2]
        tot += 1
        if r > 190 and b > 190 and g < 90:
            hit += 1
    return hit / max(tot, 1)


def magenta_ratio_pil(rect):
    """用另一种截屏路径（PIL/ImageGrab）再量一遍——分层窗口在纯 BitBlt 里可能拍不到。"""
    try:
        from PIL import ImageGrab
        x, y, w, h = rect
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        px = img.convert("RGB").load()
        hit = tot = 0
        for j in range(0, h, 3):
            for i in range(0, w, 3):
                r, g, b = px[i, j]
                tot += 1
                if r > 190 and b > 190 and g < 90:
                    hit += 1
        return hit / max(tot, 1)
    except Exception as e:
        log("PIL 截图失败:", e)
        return -1.0


def affinity_of(hwnd):
    val = wt.DWORD(0)
    user32.GetWindowDisplayAffinity.argtypes = [ctypes.c_void_p,
                                                ctypes.POINTER(wt.DWORD)]
    ok = user32.GetWindowDisplayAffinity(ctypes.c_void_p(int(hwnd)),
                                         ctypes.byref(val))
    return int(val.value) if ok else -1


def move_window(hwnd, x, y, w, h):
    user32.SetWindowPos(ctypes.c_void_p(int(hwnd)), None, x, y, w, h, 0x0004)


def main():
    winapi.set_dpi_aware()
    game_proc = subprocess.Popen([r"C:\Python314\pythonw.exe",
                                  r"G:\日语\fake_game.py"],
                                 cwd=r"G:\日语")
    hwnd_game = 0
    for _ in range(60):
        time.sleep(0.25)
        cand = windows_of(game_proc.pid)
        if cand:
            hwnd_game = cand[0]
            break
    if not hwnd_game:
        log("FAIL  假游戏窗口没起来")
        LOG.close()
        sys.exit(1)
    log("game hwnd", hwnd_game, "client", winapi.client_rect(hwnd_game))

    root = tk.Tk()
    root.geometry("20x20+0+0")
    root.update()

    cfg = FakeCfg(
        cover_bg="#ff00ff", cover_fg="#ffffff", cover_alpha=1.0,
        cover_mode=True, cover_capture="off", cover_embed=True,
        cover_match_bg=False, cover_match_size=True, cover_size_ratio=1.0,
        cover_min_size=12, cover_align="center", font_zh="Microsoft YaHei UI",
        anim_enabled=False,
    )
    cover = CoverWindow(root, cfg)
    cx, cy, cw, ch = winapi.client_rect(hwnd_game)
    cover.set_rect((cx + 80, cy + 120, min(420, cw - 100), min(110, ch - 140)))
    user32.SetForegroundWindow(ctypes.c_void_p(hwnd_game))
    time.sleep(0.2)
    cover.show("内嵌测试字幕", 24)
    time.sleep(0.6)

    # 1) detect + owner 绑定
    check("detect_game 认出另一个进程的游戏窗口", cover.detect_game() == hwnd_game,
          f"detect={cover.detect_game()} game={hwnd_game}")
    check("覆盖层被挂成游戏窗口的 owned window",
          winapi.owner_of(cover._hwnd) == hwnd_game,
          f"owner={winapi.owner_of(cover._hwnd)} game={hwnd_game}")

    # 2) 截屏排除能力仍在（顶层窗口才能设亲和）
    cover.set_capture_mode("exclude")
    time.sleep(0.2)
    check("owned 之后仍可被排除在截屏之外", affinity_of(cover._hwnd) == 0x11,
          f"affinity={affinity_of(cover._hwnd)}")
    cover.set_capture_mode("off")
    time.sleep(0.3)

    # 3) 人眼能看到
    #    注意：Tk 带 -alpha 的窗口是分层窗口，GDI BitBlt（即使 CAPTUREBLT）
    #    也拍不到它，只有 DWM 合成器（dxcam/Desktop Duplication）看得到。
    #    所以这里直接查 z 序——这才是「会不会被游戏压下去」的判据。
    log("cover 窗口矩形", winapi.window_rect(cover._hwnd),
        "affinity", affinity_of(cover._hwnd), "visible", cover.visible,
        "effective", cover.effective_rect())
    check("覆盖层排在游戏窗口上面（z 序）", above(cover._hwnd, hwnd_game),
          f"cover={cover._hwnd} game={hwnd_game}")

    # 4) 关键场景：游戏被激活 + 提成置顶，覆盖层不能被压下去
    user32.SetWindowPos(ctypes.c_void_p(hwnd_game), HWND_TOPMOST, 0, 0, 0, 0, 0x0013)
    user32.SetForegroundWindow(ctypes.c_void_p(hwnd_game))
    user32.BringWindowToTop(ctypes.c_void_p(hwnd_game))
    time.sleep(0.7)
    check("游戏抢前台后覆盖层仍在上面（owner 生效，无需抢置顶）",
          above(cover._hwnd, hwnd_game),
          f"cover={cover._hwnd} game={hwnd_game}")
    cover.raise_above()
    time.sleep(0.3)
    check("再顶一次之后仍在上面", above(cover._hwnd, hwnd_game))

    # 5) 跟随：把游戏窗口整体挪 (80, 60)
    before = cover.effective_rect()
    wx, wy, wx2, wy2 = winapi.window_rect(hwnd_game)
    old_client = winapi.client_rect(hwnd_game)
    move_window(hwnd_game, wx + 80, wy + 60, wx2 - wx, wy2 - wy)
    time.sleep(0.35)
    new_client = winapi.client_rect(hwnd_game)
    cover.follow()
    time.sleep(0.2)
    after = cover.effective_rect()
    want_dx = new_client[0] - old_client[0]
    want_dy = new_client[1] - old_client[1]
    dx, dy = after[0] - before[0], after[1] - before[1]
    check("游戏平移后覆盖层跟着走", abs(dx - want_dx) <= 2 and abs(dy - want_dy) <= 2,
          f"delta=({dx},{dy}) 应=({want_dx},{want_dy})")

    # 6) 裁剪：游戏缩到比覆盖层还小
    cx2, cy2, _cw2, _ch2 = winapi.client_rect(hwnd_game)
    move_window(hwnd_game, cx2, cy2, 360, 260)
    time.sleep(0.3)
    cover.follow()
    time.sleep(0.2)
    r = cover.effective_rect()
    ccx, ccy, ccw, cch = winapi.client_rect(hwnd_game)
    inside = (r[0] >= ccx - 1 and r[1] >= ccy - 1
              and r[0] + r[2] <= ccx + ccw + 1 and r[1] + r[3] <= ccy + cch + 1)
    check("窗口变小后覆盖层仍不越出游戏窗口", inside,
          f"cover={r} client={(ccx, ccy, ccw, cch)}")

    # 7) 最小化 / 恢复
    user32.ShowWindow(ctypes.c_void_p(hwnd_game), SW_MINIMIZE)
    time.sleep(0.5)
    cover.follow()
    check("游戏最小化时覆盖层收起", not cover.visible, f"visible={cover.visible}")
    user32.ShowWindow(ctypes.c_void_p(hwnd_game), SW_RESTORE)
    time.sleep(0.6)
    cover.follow()
    time.sleep(0.2)
    check("游戏恢复后覆盖层回来", cover.visible, f"visible={cover.visible}")

    # 8) 关掉内嵌 -> 解绑
    cover.set_embed(False)
    time.sleep(0.2)
    check("关闭内嵌后解绑 owner", winapi.owner_of(cover._hwnd) == 0,
          f"owner={winapi.owner_of(cover._hwnd)}")

    cover.destroy()
    root.destroy()
    try:
        game_proc.terminate()
    except Exception:
        pass

    log("---- 汇总 ----")
    log("失败项:", FAILS if FAILS else "无")
    LOG.close()
    sys.exit(1 if FAILS else 0)


main()
