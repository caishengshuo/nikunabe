"""定位「讲解线程卡住」的开发脚本。

    python prestart_path_check.py <config.json> [秒]

走真实 GUI 路径（tick → prestart → 流式讲解），12 秒后用 faulthandler 把所有线程
的栈打出来——打包成 windowed exe 时 stderr 被吞掉，线程里的异常/阻塞完全看不见，
只能这样抓。
"""
from __future__ import annotations

import faulthandler
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
SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 14.0

logging_dumped = []


def dump_later() -> None:
    faulthandler.dump_traceback_later(SECS, repeat=False)


cfg = Config(CFG)
cfg.load()
print(f"region={cfg.get('region')} model={cfg.get('model')} key={'有' if cfg.get('api_key') else '无'}")

events: queue.Queue = queue.Queue()
store = Store(Path("G:\\日语\\_prestart_cache.db"))
eng = Engine(cfg, store, events)
eng.ocr()

t0 = time.perf_counter()
seen: list[str] = []


def tick_loop() -> None:
    while time.perf_counter() - t0 < SECS - 1:
        try:
            eng.tick()
        except Exception as exc:  # tick 出错要打出来，windowed exe 里这段会静默
            print("tick 异常:", type(exc).__name__, exc)
        time.sleep(0.2)


threading.Thread(target=tick_loop, name="tick", daemon=True).start()
dump_later()

while time.perf_counter() - t0 < SECS:
    try:
        kind, data = events.get(timeout=0.3)
    except queue.Empty:
        continue
    ms = (time.perf_counter() - t0) * 1000
    if kind == "result":
        g = len(data.get("grammar") or [])
        v = len(data.get("vocab") or [])
        tag = "enrich" if data.get("enrich") else "base"
        line = f"{ms:7.0f}ms [{tag}] 语法{g} 生词{v}"
        print(line)
        seen.append(tag)
    elif kind == "partial":
        print(f"{ms:7.0f}ms [partial/{data.get('stage')}] {(data.get('zh') or '')[:16]}")
    else:
        print(f"{ms:7.0f}ms [{kind}] {data}")
    if "enrich" in seen and len(seen) > 2:
        break

faulthandler.cancel_dump_traceback_later()
print("讲解线程仍然存活:", any(t.name == "galtl-grammar" and t.is_alive() for t in threading.enumerate()))
eng.close()
store.close()
try:
    Path("G:\\日语\\_prestart_cache.db").unlink()
except OSError:
    pass
