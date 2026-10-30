"""Windows 底层：DPI 感知、GDI 截屏、纯 stdlib PNG 编码、置顶/穿透窗口、全局热键。

全部用 ctypes 直接调用 user32/gdi32，避免任何第三方依赖。
坐标一律使用「物理像素」（进程已设为 Per-Monitor-V2 感知）。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import queue
import struct
import threading
import zlib

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

# ---------- 常量 ----------
SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
BI_RGB = 0
DIB_RGB_COLORS = 0
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
VK_F2, VK_F3, VK_F4, VK_F6, VK_F7, VK_F8, VK_F9, VK_F10, VK_F11 = (
    0x71, 0x72, 0x73, 0x75, 0x76, 0x77, 0x78, 0x79, 0x7A,
)

# ---------- 64 位指针安全 ----------
user32.GetDC.restype = ctypes.c_void_p
user32.GetDC.argtypes = [ctypes.c_void_p]
user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.GetWindowDC.restype = ctypes.c_void_p
user32.GetWindowDC.argtypes = [ctypes.c_void_p]
user32.SetWindowPos.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, ctypes.c_uint,
]
user32.SetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.CreateDIBSection.restype = ctypes.c_void_p
gdi32.CreateDIBSection.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
    ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_uint,
]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.BitBlt.argtypes = [
    ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


# ---------- DPI ----------
def set_dpi_aware() -> None:
    """进程级 DPI 感知：必须在创建任何窗口前调用。"""
    try:
        # PER_MONITOR_AWARE_V2 = -4
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def system_dpi() -> int:
    try:
        return int(user32.GetDpiForSystem())
    except Exception:
        return 96


def dpi_scale() -> float:
    return system_dpi() / 96.0


def virtual_screen() -> tuple[int, int, int, int]:
    """整个虚拟桌面 (x, y, w, h)，可能含负坐标（副屏在左侧时）。"""
    try:
        x = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
        y = user32.GetSystemMetrics(77)
        w = user32.GetSystemMetrics(78)
        h = user32.GetSystemMetrics(79)
        if w > 0 and h > 0:
            return x, y, w, h
    except Exception:
        pass
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


# ---------- 截屏 ----------
def capture_bgra(x: int, y: int, w: int, h: int,
                 layered: bool = False) -> tuple[bytes, int, int]:
    """抓取屏幕矩形，返回 (BGRA 原始字节, w, h) —— 行序自上而下。

    ``layered=True`` 时带上 CAPTUREBLT：普通 BitBlt **拍不到分层窗口**
    （Tk 设过 ``-alpha`` 的窗口都是分层的），要验证「覆盖层到底有没有画出来」
    必须用它。OCR 路径保持默认 False——本来就不该把自己画的中文读回去。
    """
    w = max(1, int(w))
    h = max(1, int(h))
    hdc_screen = user32.GetDC(None)
    if not hdc_screen:
        raise OSError("GetDC 失败")
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h  # 负值 = 自上而下
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB
    ppv = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(
        hdc_mem, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(ppv), None, 0
    )
    if not hbmp or not ppv:
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)
        raise OSError("CreateDIBSection 失败")
    old = gdi32.SelectObject(hdc_mem, hbmp)
    try:
        flags = SRCCOPY | CAPTUREBLT if layered else SRCCOPY
        ok = gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, int(x), int(y), flags)
        if not ok:
            raise OSError("BitBlt 失败（区域超出屏幕？）")
        return ctypes.string_at(ppv, w * h * 4), w, h
    finally:
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)


def capture_hwnd_bgra(hwnd: int) -> tuple[bytes, int, int]:
    """用 PrintWindow 抓某个窗口的像素（不受遮挡影响），返回 (BGRA, w, h)。"""
    user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
    user32.PrintWindow.restype = ctypes.c_int
    user32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    rc = wt.RECT()
    if not user32.GetWindowRect(ctypes.c_void_p(int(hwnd)), ctypes.byref(rc)):
        raise OSError("GetWindowRect 失败")
    w = max(1, int(rc.right - rc.left))
    h = max(1, int(rc.bottom - rc.top))
    hdc_screen = user32.GetDC(None)
    if not hdc_screen:
        raise OSError("GetDC 失败")
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB
    ppv = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(
        hdc_mem, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(ppv), None, 0
    )
    if not hbmp or not ppv:
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)
        raise OSError("CreateDIBSection 失败")
    old = gdi32.SelectObject(hdc_mem, hbmp)
    try:
        ok = user32.PrintWindow(ctypes.c_void_p(int(hwnd)), hdc_mem, 2)  # PW_RENDERFULLCONTENT
        if not ok:
            ok = gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, int(rc.left), int(rc.top), SRCCOPY)
        if not ok:
            raise OSError("PrintWindow 与 BitBlt 都失败")
        return ctypes.string_at(ppv, w * h * 4), w, h
    finally:
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)


def grab_hwnd_png(hwnd: int, scale: float = 1.0, binarize: bool = False) -> tuple[bytes, int, int]:
    """按窗口抓图并编码 PNG（窗口被遮挡也能拍到）。"""
    buf, w, h = capture_hwnd_bgra(hwnd)
    rgb = bgra_to_rgb(buf, w, h)
    if scale and abs(scale - 1.0) > 1e-6:
        rgb, w, h = _resize_rgb(rgb, w, h, scale)
    if binarize:
        rgb = _binarize_rgb(rgb, w, h)
    return png_encode(rgb, w, h), w, h


def bgra_to_rgb(buf: bytes, w: int, h: int) -> bytes:
    """BGRA -> RGB 紧凑字节串。"""
    try:
        import numpy as np

        arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
        return arr[:, :, 2::-1].tobytes()
    except Exception:
        out = bytearray(w * h * 3)
        step = w * 4
        for row in range(h):
            src = buf[row * step : (row + 1) * step]
            base = row * w * 3
            mv = memoryview(out)
            b = src[2::4]
            g = src[1::4]
            r = src[0::4]
            mv[base : base + w * 3 : 3] = b
            mv[base + 1 : base + w * 3 : 3] = g
            mv[base + 2 : base + w * 3 : 3] = r
        return bytes(out)


def png_encode(rgb: bytes, w: int, h: int, level: int = 6) -> bytes:
    """把 RGB 原始像素编码成 PNG（无第三方库）。"""
    stride = w * 3
    raw = bytearray((stride + 1) * h)
    for y in range(h):
        raw[y * (stride + 1)] = 0  # filter: None
        raw[y * (stride + 1) + 1 : (y + 1) * (stride + 1)] = rgb[y * stride : (y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), level))
        + chunk(b"IEND", b"")
    )


def _blank_rect(rgb: bytes, w: int, h: int, rect: tuple[int, int, int, int],
                gx: int, gy: int) -> bytes:
    """把屏幕坐标 rect 覆盖的那块像素涂黑（用来排除自己的悬浮窗）。"""
    x1, y1, x2, y2 = rect
    ix1 = max(0, x1 - gx)
    iy1 = max(0, y1 - gy)
    ix2 = min(w, x2 - gx)
    iy2 = min(h, y2 - gy)
    if ix2 <= ix1 or iy2 <= iy1:
        return rgb
    ba = bytearray(rgb)
    row_bytes = (ix2 - ix1) * 3
    zeros = b"\x00" * row_bytes
    for yy in range(iy1, iy2):
        start = (yy * w + ix1) * 3
        ba[start:start + row_bytes] = zeros
    return bytes(ba)


def grab_png(x: int, y: int, w: int, h: int, scale: float = 1.0, binarize: bool = False,
             exclude: tuple[int, int, int, int] | None = None) -> tuple[bytes, int, int]:
    """截屏 -> (可选放大/二值化) -> PNG 字节。返回 (png, 输出宽, 输出高)。

    exclude 是屏幕坐标的 (x1, y1, x2, y2)：这块先被涂黑，避免把自己的悬浮窗
    连同译文一起 OCR 回来（否则每帧文本都在变，永远判不出「同一句」）。
    """
    buf, gw, gh = capture_bgra(x, y, w, h)
    rgb = bgra_to_rgb(buf, gw, gh)
    if exclude:
        rgb = _blank_rect(rgb, gw, gh, exclude, x, y)
    out_w, out_h = gw, gh
    if scale and abs(scale - 1.0) > 0.01:
        rgb, out_w, out_h = _resize_rgb(rgb, gw, gh, scale)
    if binarize:
        rgb = _binarize_rgb(rgb, out_w, out_h)
    return png_encode(rgb, out_w, out_h), out_w, out_h


def _resize_rgb(rgb: bytes, w: int, h: int, scale: float) -> tuple[bytes, int, int]:
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    try:
        import numpy as np

        arr = np.frombuffer(rgb, dtype=np.uint8).reshape(h, w, 3)
        # 双线性：先算源索引与权重
        xs = (np.arange(nw) + 0.5) / scale - 0.5
        ys = (np.arange(nh) + 0.5) / scale - 0.5
        xs = np.clip(xs, 0, w - 1)
        ys = np.clip(ys, 0, h - 1)
        x0 = np.floor(xs).astype(np.int32)
        y0 = np.floor(ys).astype(np.int32)
        x1 = np.minimum(x0 + 1, w - 1)
        y1 = np.minimum(y0 + 1, h - 1)
        wx = (xs - x0)[None, :, None]
        wy = (ys - y0)[:, None, None]
        top = arr[y0][:, x0] * (1 - wx) + arr[y0][:, x1] * wx
        bot = arr[y1][:, x0] * (1 - wx) + arr[y1][:, x1] * wx
        out = top * (1 - wy) + bot * wy
        return np.clip(out, 0, 255).astype(np.uint8).tobytes(), nw, nh
    except Exception:
        # 无 numpy：最近邻
        out = bytearray(nw * nh * 3)
        for yy in range(nh):
            sy = min(h - 1, int(yy * h / nh))
            for xx in range(nw):
                sx = min(w - 1, int(xx * w / nw))
                si = (sy * w + sx) * 3
                di = (yy * nw + xx) * 3
                out[di : di + 3] = rgb[si : si + 3]
        return bytes(out), nw, nh


def _binarize_rgb(rgb: bytes, w: int, h: int, invert: bool = False) -> bytes:
    """Otsu 全局阈值 -> 白底黑字（OCR 更稳）。numpy 缺失时原样返回。"""
    try:
        import numpy as np

        arr = np.frombuffer(rgb, dtype=np.uint8).reshape(h, w, 3).astype(np.float32)
        gray = arr @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
        hist, _ = np.histogram(gray, bins=256, range=(0, 256))
        total = gray.size
        sum_all = np.dot(np.arange(256), hist)
        sum_b = 0.0
        w_b = 0.0
        best_t, best_var = 128, -1.0
        for t in range(256):
            w_b += hist[t]
            if w_b == 0:
                continue
            w_f = total - w_b
            if w_f == 0:
                break
            sum_b += t * hist[t]
            m_b = sum_b / w_b
            m_f = (sum_all - sum_b) / w_f
            var = w_b * w_f * (m_b - m_f) ** 2
            if var > best_var:
                best_var, best_t = var, t
        dark = gray <= best_t
        if invert:
            dark = ~dark
        out = np.where(dark, 0, 255).astype(np.uint8)
        return np.repeat(out[:, :, None], 3, axis=2).tobytes()
    except Exception:
        return rgb


# ---------- 窗口控制 ----------
def hwnd_of(widget) -> int:
    """Tk 控件的顶层 HWND。"""
    return int(widget.winfo_id())


GA_PARENT = 1
GA_ROOT = 2


def top_hwnd(widget) -> int:
    """控件的顶层 HWND。

    Tk 的 ``winfo_id()`` 给的是内部子窗口；``SetWindowDisplayAffinity`` 只认顶层窗口，
    传子窗口会返回 ERROR_INVALID_PARAMETER(87)。这里一路 GetParent 走到根。
    """
    h = int(widget.winfo_id())
    seen: set[int] = set()
    desktop = int(user32.GetDesktopWindow())
    while h and h not in seen:
        seen.add(h)
        # 必须用 GA_PARENT：对顶层窗口 GetParent() 会返回 *owner*，
        # 一旦这个窗口被 set_owner 挂到别的程序上，GetParent 就会跳到对方窗口去。
        parent = user32.GetAncestor(ctypes.c_void_p(h), GA_PARENT)
        parent = int(parent or 0)
        if not parent or parent == desktop or parent == h:
            break
        h = parent
    return h


WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001
WDA_EXCLUDEFROMCAPTURE = 0x00000011


def set_capture_excluded(hwnd: int, enabled: bool = True) -> bool:
    """让窗口不被屏幕捕获拍到（Windows 10 2004+）。

    「覆盖原文」靠这一条做成：玩家屏幕上看到的是中文，而我们自己截图拿到的是
    窗口背后的原日文，于是判重/翻下一句完全不受遮挡影响，也不用闪烁取样。
    返回 False 表示系统不支持（老版本 Windows / 部分驱动），调用方要退化为隐藏取样。
    """
    try:
        flag = WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE
        return bool(user32.SetWindowDisplayAffinity(ctypes.c_void_p(int(hwnd)), flag))
    except Exception:
        return False


def place_window(hwnd: int, x: int, y: int, w: int, h: int, topmost: bool = True) -> None:
    flags = SWP_NOACTIVATE | SWP_SHOWWINDOW
    after = ctypes.c_void_p(HWND_TOPMOST & 0xFFFFFFFFFFFFFFFF) if topmost else None
    user32.SetWindowPos(ctypes.c_void_p(hwnd), after, int(x), int(y), int(w), int(h), flags)


def set_click_through(hwnd: int, enabled: bool) -> None:
    ex = user32.GetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE)
    if enabled:
        ex |= WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
    else:
        ex &= ~WS_EX_TRANSPARENT
        ex &= ~WS_EX_NOACTIVATE
    user32.SetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE, ex)


def set_tool_window(hwnd: int, enabled: bool = True) -> None:
    ex = user32.GetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE)
    ex = (ex | WS_EX_TOOLWINDOW) if enabled else (ex & ~WS_EX_TOOLWINDOW)
    user32.SetWindowLongW(ctypes.c_void_p(hwnd), GWL_EXSTYLE, ex)


def move_window(hwnd: int, x: int, y: int, w: int, h: int) -> None:
    user32.SetWindowPos(
        ctypes.c_void_p(hwnd), None, int(x), int(y), int(w), int(h), SWP_NOACTIVATE
    )


def keep_above(hwnd: int) -> bool:
    """把窗口重新提到「置顶」队首，但不夺焦点、不动位置尺寸。

    为什么需要反复调用：置顶窗口之间也有先后。游戏一被激活（点一下画面、
    全屏时按 alt-tab 回来），它就把自己提到队首，我们的覆盖层就被压在下面——
    现象就是「一点游戏，中文就被盖住了」。每次游戏抢到前台后重提一次即可。
    """
    try:
        flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
        ok = user32.SetWindowPos(
            ctypes.c_void_p(int(hwnd)),
            ctypes.c_void_p(HWND_TOPMOST & 0xFFFFFFFFFFFFFFFF),
            0, 0, 0, 0, flags,
        )
        return bool(ok)
    except Exception:
        return False


def is_fullscreen_window(hwnd: int) -> bool:
    """窗口是否铺满它所在的那块显示器（判断「独占全屏 / 无边框全屏」用）。"""
    if not hwnd:
        return False
    rect = window_rect(hwnd)
    if not rect:
        return False
    try:
        MONITOR_DEFAULTTONEAREST = 2
        mon = user32.MonitorFromWindow(ctypes.c_void_p(int(hwnd)), MONITOR_DEFAULTTONEAREST)
        if not mon:
            return False
        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                        ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(ctypes.c_void_p(mon), ctypes.byref(mi)):
            return False
        r = mi.rcMonitor
        x1, y1, x2, y2 = rect
        return (x1 <= r.left and y1 <= r.top
                and x2 >= r.right and y2 >= r.bottom)
    except Exception:
        return False


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """窗口在屏幕上的 (x1, y1, x2, y2)；失败返回 None。"""
    class RECT(ctypes.Structure):
        _fields_ = [("left", wt.LONG), ("top", wt.LONG),
                    ("right", wt.LONG), ("bottom", wt.LONG)]

    r = RECT()
    if not user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r)):
        return None
    return (int(r.left), int(r.top), int(r.right), int(r.bottom))


def foreground_hwnd() -> int:
    """当前前台窗口句柄（0 表示没有）。"""
    return int(user32.GetForegroundWindow() or 0)


def root_of(hwnd: int) -> int:
    """把任意子窗口归到它的顶层窗口；失败时返回原值。"""
    if not hwnd:
        return 0
    try:
        root = user32.GetAncestor(ctypes.c_void_p(int(hwnd)), 2)  # GA_ROOT
    except Exception:
        return int(hwnd)
    return int(root or hwnd)


def pid_of(hwnd: int) -> int:
    """窗口属于哪个进程（0 = 取不到）。"""
    if not hwnd:
        return 0
    try:
        pid = wt.DWORD(0)
        user32.GetWindowThreadProcessId(ctypes.c_void_p(int(hwnd)), ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def set_owner(hwnd: int, owner: int) -> bool:
    """把窗口挂到另一个窗口名下（owned window）。

    为什么这么做：用户抱怨「一点游戏，中文就被盖住了」。原因是置顶窗口之间仍有先后，
    游戏一激活就把自己提到队首。而 **owned window 永远排在它的 owner 之上**——
    这是窗口管理器保证的，不需要反复重提置顶，也不抢焦点。
    实测（probe_owner.py）：无 owner 时被游戏激活后覆盖层可见比例 0.000，
    设 owner 后同样是 0.107（一直可见）。
    """
    if not hwnd:
        return False
    try:
        GWLP_HWNDPARENT = -8
        user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                             ctypes.c_void_p]
        user32.SetWindowLongPtrW.restype = ctypes.c_void_p
        user32.SetWindowLongPtrW(ctypes.c_void_p(int(hwnd)), GWLP_HWNDPARENT,
                                 ctypes.c_void_p(int(owner or 0)))
        return owner_of(hwnd) == int(owner)
    except Exception:
        return False


def owner_of(hwnd: int) -> int:
    """窗口的 owner（没有则 0）。"""
    if not hwnd:
        return 0
    try:
        GWLP_HWNDPARENT = -8
        user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_void_p
        val = user32.GetWindowLongPtrW(ctypes.c_void_p(int(hwnd)), GWLP_HWNDPARENT)
        return int(val or 0)
    except Exception:
        return 0


def client_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """窗口客户区在屏幕上的 (x, y, w, h)——内嵌定位就按这个坐标系算。"""
    if not hwnd:
        return None
    class RECT(ctypes.Structure):
        _fields_ = [("left", wt.LONG), ("top", wt.LONG),
                    ("right", wt.LONG), ("bottom", wt.LONG)]
    r = RECT()
    if not user32.GetClientRect(ctypes.c_void_p(int(hwnd)), ctypes.byref(r)):
        return None
    w = int(r.right - r.left)
    h = int(r.bottom - r.top)
    point = wt.POINT(0, 0)
    if not user32.ClientToScreen(ctypes.c_void_p(int(hwnd)), ctypes.byref(point)):
        return None
    return (int(point.x), int(point.y), w, h)


def is_minimized(hwnd: int) -> bool:
    """窗口是否最小化（游戏最小化时覆盖层也该跟着藏起来）。"""
    if not hwnd:
        return False
    try:
        return bool(user32.IsIconic(ctypes.c_void_p(int(hwnd))))
    except Exception:
        return False


def is_window(hwnd: int) -> bool:
    """句柄还是不是一个有效窗口（游戏关掉后句柄会失效）。"""
    if not hwnd:
        return False
    try:
        return bool(user32.IsWindow(ctypes.c_void_p(int(hwnd))))
    except Exception:
        return False


def process_name(pid: int) -> str:
    """进程的映像文件名（如 ``SenrenBanka.exe``）；取不到返回空串。

    设置里要列出「正在运行的程序」，窗口标题常常是游戏的日文名，而进程名
    （``SenrenBanka.exe``）对用户更好认，也方便跨语言认出同一个游戏。
    """
    if not pid:
        return ""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p, wt.DWORD, ctypes.c_wchar_p, ctypes.POINTER(wt.DWORD)
        ]
        kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return ""
        try:
            size = wt.DWORD(1024)
            buf = ctypes.create_unicode_buffer(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return (buf.value or "").replace("/", "\\").split("\\")[-1]
            return ""
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        return ""


def _window_title(hwnd: int) -> str:
    try:
        n = int(user32.GetWindowTextLengthW(ctypes.c_void_p(int(hwnd))))
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(ctypes.c_void_p(int(hwnd)), buf, n + 1)
        return (buf.value or "").strip()
    except Exception:
        return ""


# 这些进程的窗口永远不可能是「要翻译的游戏」，出现在选择列表里只会干扰
_SYSTEM_EXES = {
    "textinputhost.exe", "applicationframehost.exe", "systemsettings.exe",
    "shellexperiencehost.exe", "startmenuexperiencehost.exe", "searchhost.exe",
    "searchapp.exe", "lockapp.exe", "taskmgr.exe", "sihost.exe",
}


def _is_tool_window(hwnd: int) -> bool:
    """WS_EX_TOOLWINDOW（输入法候选框、托盘图标那类）不该出现在选择列表里。"""
    try:
        GWL_EXSTYLE = -20
        WS_EX_TOOLWINDOW = 0x00000080
        user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        style = int(user32.GetWindowLongW(ctypes.c_void_p(int(hwnd)), GWL_EXSTYLE))
        return bool(style & WS_EX_TOOLWINDOW)
    except Exception:
        return False


def _enumerate_visible() -> list[int]:
    out: list[int] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _lp):
        if user32.IsWindowVisible(ctypes.c_void_p(hwnd)):
            out.append(int(hwnd))
        return True

    if not user32.EnumWindows.argtypes:
        user32.EnumWindows.argtypes = [ctypes.c_void_p, wt.LPARAM]
    try:
        user32.EnumWindows(cb, 0)
    except Exception:
        return []
    return out


def list_targets(exclude_pids=(), min_w: int = 320, min_h: int = 180,
                 skip_system: bool = True) -> list[dict]:
    """列出「可以当识别目标」的窗口，按面积从大到小。

    每项：``{hwnd, pid, exe, title, client, area}``；``client`` 是客户区的
    屏幕矩形 ``(x, y, w, h)``——抓字就是按它截的，不含标题栏和边框。

    过滤规则（都是为了把真正能玩的窗口筛出来）：
      * 可见、有标题文字、不是 WS_EX_TOOLWINDOW；
      * 客户区至少 ``min_w × min_h``（对话框都装不下的碎窗没意义）；
      * 同一进程只留最大的那个（游戏常有启动器小窗 + 主窗）；
      * 排除自己（``exclude_pids`` 里传本进程 pid）。
    """
    skip = {int(p) for p in exclude_pids if p}
    best: dict[int, dict] = {}
    for hwnd in _enumerate_visible():
        if _is_tool_window(hwnd):
            continue
        title = _window_title(hwnd)
        if not title:
            continue
        pid = pid_of(hwnd)
        if not pid or pid in skip:
            continue
        rect = client_rect(hwnd)
        if not rect:
            continue
        _x, _y, w, h = rect
        if w < min_w or h < min_h:
            continue
        exe = process_name(pid)
        if skip_system and exe.lower() in _SYSTEM_EXES:
            continue
        item = {
            "hwnd": hwnd,
            "pid": pid,
            "exe": exe,
            "title": title,
            "client": rect,
            "area": int(w) * int(h),
        }
        old = best.get(pid)
        if old is None or item["area"] > old["area"]:
            best[pid] = item
    return sorted(best.values(), key=lambda d: (-d["area"], d["title"]))


def hwnd_for_pid(pid: int, min_w: int = 240, min_h: int = 140) -> int:
    """某进程当前的主窗口（可见、面积最大）；进程没窗口时返回 0。

    识别目标按 **pid** 存，而不是存 hwnd：游戏重启后句柄会变，pid 也可能变，
    但 pid 一致时用它重新找窗口就能立刻对上（覆盖层跟随也靠它）。
    """
    pid = int(pid or 0)
    if not pid:
        return 0
    best_hwnd = 0
    best_area = 0
    for hwnd in _enumerate_visible():
        if pid_of(hwnd) != pid or _is_tool_window(hwnd):
            continue
        rect = client_rect(hwnd)
        if not rect:
            continue
        _x, _y, w, h = rect
        if w < min_w or h < min_h:
            continue
        area = int(w) * int(h)
        if area > best_area:
            best_area = area
            best_hwnd = hwnd
    return best_hwnd


def windows_containing(x: int, y: int, w: int, h: int,
                       exclude_pid: int = 0) -> list[int]:
    """所有「能完整装下这块矩形」的可见顶层窗口，按面积从小到大。

    为什么不用 WindowFromPoint：覆盖层自己就盖在识别区上，点到的是我们自己。
    这里改成纯几何匹配——谁的窗口范围装得下这块区域，谁就是候选宿主；
    面积最小的那个通常就是游戏窗口（而不是全屏的浏览器/桌面）。
    """
    out: list[tuple[int, int]] = []
    pid_self = int(exclude_pid or 0)

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _lp):
        if not user32.IsWindowVisible(ctypes.c_void_p(hwnd)):
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == pid_self:
            return True
        rect = window_rect(int(hwnd))
        if not rect:
            return True
        x1, y1, x2, y2 = rect
        if x1 <= x and y1 <= y and x2 >= x + w and y2 >= y + h:
            out.append(((x2 - x1) * (y2 - y1), int(hwnd)))
        return True

    if not user32.EnumWindows.argtypes:
        user32.EnumWindows.argtypes = [ctypes.c_void_p, wt.LPARAM]
    try:
        user32.EnumWindows(cb, 0)
    except Exception:
        return []
    out.sort()
    return [hwnd for _area, hwnd in out]


def window_at(x: int, y: int) -> int:
    """屏幕坐标处最上层窗口的顶层句柄（用来认「这块像素属于哪个程序」）。"""
    point = wt.POINT(int(x), int(y))
    hwnd = user32.WindowFromPoint(point)
    if not hwnd:
        return 0
    root = user32.GetAncestor(ctypes.c_void_p(hwnd), 2)  # GA_ROOT
    return int(root or hwnd)


def sample_frame(x: int, y: int, w: int, h: int, nx: int = 64, ny: int = 16) -> bytes:
    """把区域缩小成 64x16 的 BGRA 原始像素（每像素 4 字节）。"""
    w = max(1, int(w))
    h = max(1, int(h))
    nx = max(4, min(256, int(nx)))
    ny = max(4, min(128, int(ny)))
    hdc_screen = user32.GetDC(None)
    if not hdc_screen:
        raise OSError("GetDC 失败")
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = nx
    bmi.bmiHeader.biHeight = -ny
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB
    ppv = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(
        hdc_mem, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(ppv), None, 0
    )
    if not hbmp or not ppv:
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)
        raise OSError("CreateDIBSection 失败")
    old = gdi32.SelectObject(hdc_mem, hbmp)
    try:
        gdi32.SetStretchBltMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
        gdi32.StretchBlt.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_uint,
        ]
        gdi32.StretchBlt.restype = ctypes.c_int
        gdi32.SetStretchBltMode(hdc_mem, 3)  # HALFTONE
        ok = gdi32.StretchBlt(hdc_mem, 0, 0, nx, ny, hdc_screen, int(x), int(y), w, h, SRCCOPY)
        if not ok:
            raise OSError("StretchBlt 失败（区域超出屏幕？）")
        return ctypes.string_at(ppv, nx * ny * 4)
    finally:
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)


def frame_diff(a: bytes, b: bytes, tol: int = 12) -> float:
    """两张小帧之间「差别有多大」：返回变化像素的占比（0~1）。

    为什么要占比而不是「是否相同」：galgame 的对话框里常年有闪烁的 ▼ 箭头、
    呼吸立绘、雪花粒子，像素每帧都在变；按「变了就 OCR」会每秒 OCR 五次，
    而按「变了多少」就能把箭头那种小面积闪烁过滤掉，只对真正的换行做出反应。
    """
    if not a or not b or len(a) != len(b):
        return 1.0
    n = len(a) // 4
    if n <= 0:
        return 1.0
    changed = 0
    try:
        import numpy as np

        x = np.frombuffer(a, dtype=np.uint8).reshape(-1, 4).astype(np.int16)
        y = np.frombuffer(b, dtype=np.uint8).reshape(-1, 4).astype(np.int16)
        changed = int((np.abs(x[:, :3] - y[:, :3]).max(axis=1) > tol).sum())
    except Exception:
        for i in range(0, len(a), 4):
            if (abs(a[i] - b[i]) > tol or abs(a[i + 1] - b[i + 1]) > tol
                    or abs(a[i + 2] - b[i + 2]) > tol):
                changed += 1
    return changed / float(n)


def sample_hash(x: int, y: int, w: int, h: int, nx: int = 64, ny: int = 16) -> str:
    """小帧的哈希（保留给需要「变没变」布尔判断的调用方）。"""
    import hashlib

    return hashlib.sha1(sample_frame(x, y, w, h, nx, ny)).hexdigest()


# ---------- 全局热键 ----------
class HotkeyThread(threading.Thread):
    """后台线程注册全局热键，按键事件通过 queue 交给 Tk 主线程。

    tkinter 窗口失焦时 bind 收不到按键，galgame 全屏前台必须用 RegisterHotKey。
    """

    daemon = True

    def __init__(self, mapping: dict[int, str], sink: "queue.Queue[str]"):
        super().__init__(name="galtl-hotkeys")
        self.mapping = dict(mapping)
        self.sink = sink
        self.registered: list[tuple[int, str]] = []
        self.error: str | None = None
        self._ready = threading.Event()
        self._tid: int | None = None

    def run(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentThreadId.restype = wt.DWORD
        self._tid = int(kernel32.GetCurrentThreadId())
        failed = []
        for hid, (vk, name) in enumerate(self.mapping.items(), start=1):
            if not user32.RegisterHotKey(None, hid, MOD_NOREPEAT, int(vk)):
                failed.append(name)
            else:
                self.registered.append((hid, name))
        if failed:
            self.error = "热键被占用：" + "、".join(failed)
        self._ready.set()
        if not self.registered:
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                name = dict(self.registered).get(int(msg.wParam))
                if name:
                    self.sink.put(name)
        for hid, _ in self.registered:
            user32.UnregisterHotKey(None, hid)

    def wait_ready(self, timeout: float = 3.0) -> bool:
        return self._ready.wait(timeout)

    def stop(self) -> None:
        try:
            if self._tid:
                user32.PostThreadMessageW(int(self._tid), 0x0012, 0, 0)  # WM_QUIT
        except Exception:
            pass
