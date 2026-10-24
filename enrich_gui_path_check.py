"""开发自检：走和 GUI 完全相同的引擎路径，看「译文之后」的讲解事件有没有真到。

    python enrich_gui_path_check.py

不启动 Tk，只用 Engine + 事件队列：translate_now() 之后打印每条事件的
grammar/vocab 条数，确认真机（含打包 exe）里流式讲解会一条条补上。
"""
from __future__ import annotations

import json
import queue
import sys
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

cfg = Config(CFG)
cfg.load()
print(f"配置 {CFG}  model={cfg.get('model')}  grammar_phase={cfg.get('grammar_phase')}"
      f"  grammar_async={cfg.get('grammar_async')}  key={'有' if cfg.get('api_key') else '无'}")

events: queue.Queue = queue.Queue()
store = Store(Path("G:\\日语\\_gui_path_cache.db"))
eng = Engine(cfg, store, events)
eng.ocr()

t0 = time.perf_counter()
SAMPLE = "べ、別にアンタのことなんか待ってないんだからねっ！"
if len(sys.argv) > 2:
    SAMPLE = sys.argv[2]
eng.translate(SAMPLE)
seen = 0
last = None
while time.perf_counter() - t0 < 20:
    try:
        kind, data = events.get(timeout=0.4)
        ts = time.perf_counter()
    except queue.Empty:
        continue
    seen += 1
    if kind == "result":
        g = len(data.get("grammar") or [])
        v = len(data.get("vocab") or [])
        tag = "enrich" if data.get("enrich") else "base"
        last = ts - t0
        print(f"  {(ts - t0) * 1000:7.0f}ms [{tag}] 语法{g} 生词{v}  zh={data.get('zh', '')[:18]}")
        if g >= 3 and v >= 3 and data.get("enrich"):
            break
    elif kind in ("status", "error"):
        print(f"  {(ts - t0) * 1000:7.0f}ms [{kind}] {data}")

eng.close()
store.close()
try:
    Path("G:\\日语\\_gui_path_cache.db").unlink()
except OSError:
    pass
print(f"事件数 {seen}，最后一次 {(last or 0) * 1000:.0f}ms",
      "-> 讲解已补上" if seen >= 2 else "-> 只有一条，讲解没到")
