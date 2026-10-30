import sys as _sys
if _sys.stdout is None:          # pythonw 下 stdout 是 None，直接 print 会崩
    _sys.stdout = open("NUL", "w", encoding="utf-8")
if _sys.stderr is None:
    _sys.stderr = open("NUL", "w", encoding="utf-8")
import io, sys, time
sys.path.insert(0, r"G:\日语")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import queue
from galtl.config import Config
from galtl.pipeline import Engine
from galtl.store import Store

FULL = "お客さんってば、こんな時間まで起きているなんて、明日の朝が心配だよ。"

cfg = Config(); cfg.load()
cfg.set("mode", "ocr"); cfg.set("settle_ms", 200)
cfg.set("stable_reads", 2); cfg.set("use_probe", False)
cfg.set("min_ratio", 0.1); cfg.set("use_cache", False)

store = Store(":memory:")
eng = Engine(cfg, store, queue.Queue())
eng.translate = lambda jp, png=None: {"jp": jp, "zh": "（测试译文）", "grammar": [], "vocab": [], "meta": {}}

frames = []
t = 0.0; n = 3
while n < len(FULL):
    frames.append((t, FULL[:n])); n += 3; t += 0.11
for _ in range(8):
    frames.append((t, FULL)); t += 0.11

state = {"i": 0, "clock": 0.0}
def fake_grab():
    i = state["i"]
    if i >= len(frames):
        return (FULL, b"", 50.0)
    tt, txt = frames[i]; state["i"] = i + 1; state["clock"] = tt
    return (txt, b"", 50.0)
eng.grab_text = fake_grab

real_time = time.time
time.time = lambda: 1000.0 + state["clock"]

results = []
for _ in range(len(frames) + 6):
    r = eng.tick(force=False)
    if r:
        results.append((round(state["clock"], 2), r["jp"]))
        break
time.time = real_time

strip = lambda s: s.replace("。", "").replace(" ", "")
print("翻译次数：", len(results), results)
ok = len(results) == 1 and strip(results[0][1]) == strip(FULL)
print("打字机中途不翻、整句才翻：", "通过" if ok else "失败")
eng.close(); store.close()
raise SystemExit(0 if ok else 1)
