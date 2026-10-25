"""验证「省 CPU」的三件事真的生效，并且没有把功能弄坏。

1. sample_hash：画面不动 → 指纹不变；画面变了 → 指纹变。
2. 前台门：前台不是识别区所属窗口时 tick() 一次屏都不抓（返回 None）；
   前台回到该窗口后立刻恢复翻译。（前台用 monkeypatch 模拟，免得和 Windows
   的抢焦点规则打架——真机上的抢焦点已经用 SetForegroundWindow 验证过。）
3. 空闲放慢：连续几轮画面不变后 `_idle_ticks` 会增加（_loop 据此拉长间隔）。
"""

from __future__ import annotations

import ctypes
import queue
import sys
import time
import tkinter as tk

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from galtl import winapi
from galtl.config import Config
from galtl.pipeline import Engine
from galtl.store import Store

OK = "  [ok] "
BAD = "  [!!] "
fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print((OK if cond else BAD) + msg)
    if not cond:
        fails.append(msg)


def main() -> int:
    winapi.set_dpi_aware()
    root = tk.Tk()
    root.overrideredirect(True)          # 去掉标题栏：winfo_rootx/y 就是客户区原点
    root.geometry("900x220+240+1080")
    lab = tk.Label(root, text="", font=("Yu Gothic UI", 22), bg="#101820", fg="#f0f0f0",
                   wraplength=860, justify="left", anchor="w")
    lab.pack(fill="both", expand=True)
    root.update()
    root.lift()
    root.attributes("-topmost", True)
    time.sleep(0.3)
    root.update()

    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    region = [x, y, w, h]
    print("假游戏区域", region)

    # ---- 1. 指纹 ----
    lab.configure(text="こんな時間まで起きているなんて")
    root.update()
    time.sleep(0.25)
    a1 = winapi.sample_hash(x, y, w, h)
    time.sleep(0.05)
    a2 = winapi.sample_hash(x, y, w, h)
    lab.configure(text="まったく、明日の朝が心配だよ")
    root.update()
    time.sleep(0.3)
    b = winapi.sample_hash(x, y, w, h)
    check(a1 == a2, "画面不动时指纹稳定")
    check(a1 != b, "画面变化时指纹会变")

    # ---- 引擎 ----
    cfg = Config(r"C:\Users\21298\AppData\Local\Temp\perf_gate_config.json")
    cfg.load()
    cfg.data.update({
        "mode": "ocr", "region": region, "poll_ms": 200, "use_probe": True,
        "only_foreground": True, "idle_after_ticks": 3, "idle_poll_ms": 600,
        "stable_reads": 1, "settle_ms": 0, "upscale": 1.0, "binarize": False,
        "auto_translate": False, "ocr_engine": "winocr", "ocr_lang": "ja",
        "translate_engine": "llm", "base_url": "", "model": "", "api_key": "",
        "min_ratio": 0.0, "context_lines": 0, "glossary": "",
        "grammar_phase": False, "grammar_async": False, "stream_translate": False,
    })
    store = Store(r"C:\Users\21298\AppData\Local\Temp\perf_gate_cache.db")
    events: queue.Queue = queue.Queue()
    eng = Engine(cfg, store, events)
    eng.set_region(region, winapi.hwnd_of(root))
    game_hwnd = eng._game_hwnd
    print("记下的窗口 hwnd =", game_hwnd)

    # ---- 2. 前台门（monkeypatch 前台窗口） ----
    real_fg = winapi.foreground_hwnd

    def foreign_hwnd() -> int:
        """拿一个**别的进程**的窗口句柄：桌面窗口（pid=0）就是最稳的那个。"""
        try:
            winapi.user32.GetDesktopWindow.restype = ctypes.c_void_p
            return int(winapi.user32.GetDesktopWindow() or 1)
        except Exception:
            return 1

    other_hwnd = foreign_hwnd()
    print("另外的进程的窗口 hwnd =", other_hwnd, "pid =", winapi.pid_of(other_hwnd))

    sk0 = eng._skipped_ticks
    winapi.foreground_hwnd = lambda: other_hwnd
    got = eng.tick()
    check(got is None, "前台是别的程序时 tick() 不翻译")
    check(eng._skipped_ticks > sk0, f"计入跳过计数（{sk0}→{eng._skipped_ticks}）")

    # 我们自己的窗口（字幕条/主窗口/设置）也算「还在游戏里」，否则一点字幕条就停翻
    winapi.foreground_hwnd = lambda: winapi.hwnd_of(root)
    check(eng._in_foreground() is True, "前台是自己的窗口时判定为在场")
    sk1 = eng._skipped_ticks
    eng.tick()
    check(eng._skipped_ticks == 0 and sk1 <= 1, f"自己的窗口不会累加跳过（{sk1}→{eng._skipped_ticks}）")

    winapi.foreground_hwnd = lambda: game_hwnd
    lab.configure(text="お客さんってば、聞いてるの？")
    root.update()
    time.sleep(0.3)
    res = None
    for _ in range(3):
        r = eng.tick()
        if r:
            res = r
            break
        time.sleep(0.1)
        root.update()
    jp = (res or {}).get("jp_ocr") or ""
    check("お客さん" in jp, f"前台回到识别区窗口后恢复翻译：{jp!r}")
    check(eng._skipped_ticks == 0, "回到前台后跳过计数清零")

    # ---- 3. 空闲放慢（保持前台=识别区窗口，否则会被前台门挡在前面）----
    for _ in range(5):
        eng.tick()
        time.sleep(0.03)
        root.update()
    check(eng._idle_ticks >= 3, f"画面不动时 _idle_ticks 累加（={eng._idle_ticks}）")

    # ---- 4. 小面积闪烁（▼ 箭头/呼吸立绘）不该触发 OCR ----
    blink = tk.Frame(root, width=48, height=48, bg="#ff3b30")
    f0 = winapi.sample_frame(x, y, w, h)
    eng._probe_frame = f0
    eng._idle_ticks = 0
    blink.place(x=20, y=20)
    root.update()
    time.sleep(0.25)
    f1 = winapi.sample_frame(x, y, w, h)
    d = winapi.frame_diff(f0, f1)
    print("  闪烁块造成的画面变化占比 = %.4f" % d)
    check(0.0 < d < float(cfg.get("probe_min_change", 0.02)) * 1.5,
          f"小面积闪烁的变化占比很小（{d:.4f}）")
    got = eng.tick()
    check(got is None, "小面积闪烁时 tick() 不翻译（被变化阈值挡掉）")

    # ---- 5. 整行换词要能触发 ----
    lab.configure(text="もう遅いから、そろそろ寝なさいよ")
    root.update()
    time.sleep(0.3)
    res = None
    for _ in range(4):
        r = eng.tick()
        if r:
            res = r
            break
        time.sleep(0.1)
        root.update()
    jp2 = (res or {}).get("jp_ocr") or ""
    check("そろそろ寝なさい" in jp2, f"整行换词仍能触发翻译：{jp2!r}")

    winapi.foreground_hwnd = real_fg

    eng.close()
    try:
        store.close()
    except Exception:
        pass
    root.destroy()

    print()
    if fails:
        print("失败 %d 项：" % len(fails))
        for f in fails:
            print("  -", f)
        return 1
    print("全部通过：指纹、前台门、空闲放慢")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
