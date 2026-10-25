# -*- coding: utf-8 -*-
"""一点点补间/节流小工具（纯 Tk，无第三方依赖）。

为什么要它：悬浮字幕条是「流式」上屏的，一句台词可能在一秒内被改写十几次。
以前每次改动都走「删掉全文 → 重新 insert → 重新量高度 → 重设窗口几何」，
在 Tk 里等于每帧都要重新排版 + 搬窗口，肉眼看到的就是「字母一个个蹦出来还掉帧」。

这里只做两件事：
* `Coalesce`：把同一帧内的一串小改动合并成一次重绘（默认 16ms 一帧）；
* `Tween`：给一个 0→1 的进度回调（缓出曲线），用来做颜色淡入、透明度淡入。
两者都是「单飞」的——重复调用不会叠出一堆 after 定时器。
"""
from __future__ import annotations

import time

FRAME_MS = 16


def ease_out_cubic(t: float) -> float:
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    return 1.0 - (1.0 - t) ** 3


def mix(c1: str, c2: str, t: float) -> str:
    """两个 #rrggbb 颜色按 t 混合（t=0 取 c1）。"""
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    try:
        a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    except Exception:
        return c2
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


class Coalesce:
    """把短时间内的多次请求合并成一次（下一次 after 触发时执行最新的那个）。"""

    def __init__(self, widget, ms: int = FRAME_MS):
        self.widget = widget
        self.ms = ms
        self._id = None
        self._pending = None

    def call(self, fn) -> None:
        self._pending = fn
        if self._id is not None:
            return
        try:
            self._id = self.widget.after(self.ms, self._fire)
        except Exception:
            self._id = None

    def _fire(self) -> None:
        self._id = None
        fn, self._pending = self._pending, None
        if fn is None:
            return
        try:
            fn()
        except Exception:
            pass

    def cancel(self) -> None:
        if self._id is not None:
            try:
                self.widget.after_cancel(self._id)
            except Exception:
                pass
            self._id = None
        self._pending = None


class Tween:
    """0→1 的进度动画；每帧回调一次 on_frame(t)。"""

    def __init__(self, widget, ms: int = 140, on_frame=None, on_done=None,
                 ease=ease_out_cubic, interval: int = FRAME_MS):
        self.widget = widget
        self.ms = max(1, int(ms))
        self.on_frame = on_frame
        self.on_done = on_done
        self.ease = ease
        self.interval = max(1, int(interval))
        self._id = None
        self._t0 = 0.0
        self._dead = False

    def start(self) -> None:
        self.cancel()
        self._dead = False
        self._t0 = time.perf_counter()
        self._tick()

    def _tick(self) -> None:
        if self._dead:
            return
        now = time.perf_counter()
        raw = (now - self._t0) * 1000.0 / self.ms
        t = self.ease(raw)
        if self.on_frame:
            try:
                self.on_frame(t)
            except Exception:
                self._dead = True
                return
        if raw >= 1.0:
            self._id = None
            if self.on_done:
                try:
                    self.on_done()
                except Exception:
                    pass
            return
        try:
            self._id = self.widget.after(self.interval, self._tick)
        except Exception:
            self._id = None

    def cancel(self) -> None:
        self._dead = True
        if self._id is not None:
            try:
                self.widget.after_cancel(self._id)
            except Exception:
                pass
            self._id = None
