"""端到端时间线：从「画面变了」到「看见中文」到底多少毫秒。

不动真屏，直接把 Engine 的 screen 通道换掉（探针给假哈希、OCR 给固定台词），
其余全部走真实链路：真 config、真 API、真两段式 + 异步语法讲解。

用法：python latency_e2e.py
"""
from __future__ import annotations

import queue
import time

from galtl.config import Config
from galtl.pipeline import Engine
from galtl.store import Store

LINES = [
    "こんな時間まで起きているなんて、明日の朝が心配だよ……まったく。",
    "別に、あなたのことを待っていたわけじゃないんだから。",
]


class FakeEngine(Engine):
    """把屏幕换成脚本：每次 tick 由调用方决定「画面变没变」。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.frame = 0          # 第几帧
        self.screen = 0         # 当前画面编号（变了就代表新画面）
        self.line = LINES[0]

    def probe_hash(self) -> str:
        return f"screen-{self.screen}"

    def grab_text(self):
        self.frame += 1
        return self.line, b"\x89PNG fake", 12.0


def drain(q: queue.Queue, until, timeout: float) -> list[tuple[float, str, dict]]:
    """收集事件直到 until(kind, data) 为真或超时，返回 [(相对时间, kind, data)]。"""
    out = []
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        try:
            kind, data = q.get(timeout=0.05)
        except queue.Empty:
            continue
        out.append((time.perf_counter(), kind, data))
        if until(kind, data):
            break
    return out


def main() -> int:
    cfg = Config()
    cfg.load()
    if not cfg.has_key:
        print("config.json 里没有 api_key，测不了网络段")
        return 1
    cfg.set("use_cache", False)          # 缓存会把一轮压成 0ms，测不出真实延迟
    store = Store()
    q: queue.Queue = queue.Queue()
    eng = FakeEngine(cfg, store, q)

    print("=== 第一句：从「画面变了」到「看见中文」 ===")
    t0 = time.perf_counter()
    seq = []

    def step() -> None:
        """跑一轮 tick（模拟一次轮询），把事件时间戳记下来。"""
        eng.tick()
        for _ in range(40):
            try:
                kind, data = eng.events.get_nowait()
            except queue.Empty:
                break
            seq.append(((time.perf_counter() - t0) * 1000, kind, data))

    step()                      # 帧 1：发现变化 → OCR → 记住待确认
    time.sleep(0.08)
    step()                      # 帧 2：画面没变 → 开始翻译（阻塞到译文流结束）

    first_partial = next((t for t, k, _ in seq if k == "partial"), None)
    result = next(((t, d) for t, k, d in seq if k == "result"), None)
    print(f"  首个译文增量   {first_partial or 0:7.0f} ms")
    if result:
        t, d = result
        print(f"  译文+本地语法  {t:7.0f} ms   {d.get('zh', '')[:26]}")
        print(f"  本轮翻译耗时   {d.get('meta', {}).get('ms', 0):7.0f} ms"
              f"（首字 {d.get('meta', {}).get('first_ms')} ms）")

    # 等异步讲解
    deadline = time.perf_counter() + 20
    enrich = None
    while time.perf_counter() < deadline:
        try:
            kind, data = q.get(timeout=0.2)
        except queue.Empty:
            continue
        if kind == "result" and data.get("enrich"):
            enrich = ((time.perf_counter() - t0) * 1000, data)
            break
    if enrich:
        t, d = enrich
        print(f"  语法讲解补上   {t:7.0f} ms   {len(d.get('grammar') or [])} 条 / "
              f"{len(d.get('vocab') or [])} 词")

    print()
    print("=== 第二句：讲解还在跑的时候换台，看主循环有没有被堵 ===")
    eng.screen = 1
    eng.line = LINES[1]
    t0 = time.perf_counter()
    eng.tick()
    time.sleep(0.08)
    t1 = time.perf_counter()
    eng.tick()
    blocked = (time.perf_counter() - t1) * 1000
    print(f"  第二句 tick 阻塞   {blocked:7.0f} ms（旧版会被上一句的语法请求堵住 3s 左右）")

    eng.close()
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
