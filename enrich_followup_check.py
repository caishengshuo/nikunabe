"""回归：换句之后，讲解仍然会启动（busy 不能被永久卡住）。

    python enrich_followup_check.py <config.json> [秒]

背景（1.0.8 实测）：`_enrich_run` 在「玩家已翻页」的分支里提前 return，把
`_enrich_busy` 永久留在 True 上——之后每句讲解都塞进 `_enrich_next` 再没人启动，
界面表现是「语法永远只有本地那两条」。这里连着跑几句，要求至少 2 个不同台词
都拿到过 enrich 事件。
"""
from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

from galtl.config import Config
from galtl.pipeline import Engine
from galtl.store import Store

CFG = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("G:\\日语\\config.json")
SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 26.0

cfg = Config(CFG)
cfg.load()
cfg.set("use_cache", False)       # 别让缓存把第二次测试挡掉

events: queue.Queue = queue.Queue()
store = Store(Path("G:\\日语\\_followup_cache.db"))
eng = Engine(cfg, store, events)
eng.ocr()

t0 = time.perf_counter()


def tick_loop() -> None:
    while time.perf_counter() - t0 < SECS:
        try:
            eng.tick()
        except Exception as exc:
            print("tick 异常:", type(exc).__name__, exc)
        time.sleep(0.2)


threading.Thread(target=tick_loop, name="tick", daemon=True).start()

enriched: dict[str, int] = {}
base_seen = 0
while time.perf_counter() - t0 < SECS:
    try:
        kind, data = events.get(timeout=0.3)
    except queue.Empty:
        continue
    if kind != "result":
        continue
    jp = (data.get("jp") or "").strip().split("\n")[-1][:14]
    if data.get("enrich"):
        enriched[jp] = max(enriched.get(jp, 0), len(data.get("grammar") or []))
    else:
        base_seen += 1
        print(f"{(time.perf_counter() - t0) * 1000:7.0f}ms base 语法{len(data.get('grammar') or [])} {jp}")

print(f"\nbase 帧 {base_seen} 次；拿到流式讲解的台词 {len(enriched)} 句：")
for jp, n in enriched.items():
    print(f"  {jp} -> 语法{n}条")

eng.close()
store.close()
try:
    Path("G:\\日语\\_followup_cache.db").unlink()
except OSError:
    pass
ok = len(enriched) >= 2
print("结论：", "换句后讲解仍会启动" if ok else "换句后讲解被卡住（busy 未复位）")
sys.exit(0 if ok else 1)
