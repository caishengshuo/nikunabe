"""关闭竞态回归：自动模式跑到一半直接 close()，不应产生 WARNING/AttributeError。"""
import io
import logging
import queue
import sys
import time

from galtl.config import Config
from galtl.pipeline import Engine
from galtl.store import Store

buf = io.StringIO()
logging.basicConfig(stream=buf, level=logging.DEBUG,
                    format="%(levelname)-7s %(name)s | %(message)s")

cfg = Config()
cfg.set("auto_translate", True)
cfg.set("region", [0, 0, 2560, 1600])
cfg.set("upscale", 1.0)
events: "queue.Queue" = queue.Queue()
store = Store("G:\\日语\\history.db")
eng = Engine(cfg, store, events)

eng.start_auto()
time.sleep(1.2)
eng.close()          # 模拟关窗口：正在抓取时把 worker 掐掉
time.sleep(1.5)
store.close()

out = buf.getvalue()
warn = [l for l in out.splitlines() if "WARNING" in l or "ERROR" in l]
print("自动帧数：", eng.stats["ocr"], "｜ 日志行：", len(out.splitlines()), "｜ 告警：", len(warn))
for l in warn[:5]:
    print("  ", l)
print("结论：", "干净退出" if not warn else "仍有告警，需继续修")
sys.exit(0 if not warn else 1)
