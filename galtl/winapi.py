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
VK_F2, VK_F3, VK_F4, VK_F6, VK_F7, VK_F8, VK_F9, VK_F10 = (
    0x71, 0x72, 0x73, 0x75, 0x76, 0x77, 0x78, 0x79,
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
def capture_bgra(x: int, y: int, w: int, h: int) -> tuple[bytes, int, int]:
    """抓取屏幕矩形，返回 (BGRA 原始字节, w, h) —— 行序自上而下。"""
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
        ok = gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, int(x), int(y), SRCCOPY)
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


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """窗口在屏幕上的 (x1, y1, x2, y2)；失败返回 None。"""
    class RECT(ctypes.Structure):
        _fields_ = [("left", wt.LONG), ("top", wt.LONG),
                    ("right", wt.LONG), ("bottom", wt.LONG)]

    r = RECT()
    if not user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r)):
        return None
    return (int(r.left), int(r.top), int(r.right), int(r.bottom))


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
