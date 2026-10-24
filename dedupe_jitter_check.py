"""防抖回归：同一句台词的 OCR 抖动不能被当成「新台词」。

    python dedupe_jitter_check.py

背景（1.0.8 实测）：假游戏里闪烁的 ▼ 偶尔被 OCR 认成假名，stable_key 每帧都不同，
于是每帧 _seq+1，正在流式补讲的语法被判定成「玩家已翻页」而整份丢掉——界面表现
就是「语法只有本地那两条，模型讲解永远不出现」。这里锁住两件事：
  1) similar_key 认得抖动、不把不同台词算成同一句；
  2) tick() 遇到抖动文本时不重复翻译、不推进 _seq。
"""
from __future__ import annotations

import queue
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

from galtl.config import Config
from galtl.pipeline import Engine
from galtl.store import Store
from galtl.textutil import similar_key, stable_key

BASE = "べ、別にアンタのことなんか待ってないんだからねっ"
JITTER = "べ、別にアンタのことなんか待ってないんだからねっマ"   # ▼ 被认成「マ」
OTHER = "こんな時間まで起きているなんて、明日の朝が心配だよ……まったく。"

cases = [
    ("完全相同", BASE, BASE, True),
    ("末尾多认出一个假名", BASE, JITTER, True),
    ("不同台词", BASE, OTHER, False),
    ("太短的两段", "はい", "はぃ", False),
    ("长度差太多", BASE, BASE[:8], False),
]
ok = True
for name, a, b, want in cases:
    got = similar_key(stable_key(a), stable_key(b))
    flag = "通过" if got == want else "失败"
    ok = ok and got == want
    print(f"  [{'ok' if got == want else '!!'}] {name}: similar_key={got} 期望={want} -> {flag}")

# ---- tick 层面：抖动文本不重复翻译、不推进 _seq ----
cfg = Config(Path("G:\\日语\\_dedupe_test.json"))
cfg.set("mode", "ocr")
cfg.set("use_probe", False)
cfg.set("stable_reads", 1)
cfg.set("provider", "none")      # 不发请求，只看去重与计数
cfg.set("use_cache", False)
cfg.set("auto_translate", True)
cfg.set("grammar_phase", False)

store = Store(Path("G:\\日语\\_dedupe_test.db"))
events: queue.Queue = queue.Queue()
eng = Engine(cfg, store, events)
eng._exclude_rect = lambda: None  # type: ignore[assignment]

texts = iter([BASE, JITTER, JITTER, OTHER])
eng.grab_text = lambda: (next(texts), None, 1.0)  # type: ignore[assignment]

results = []
for _ in range(4):
    r = eng.tick()
    if r:
        results.append(r.get("jp") or r.get("jp_ocr") or "")
    events.queue.clear()

seq_after = eng._seq
dup = sum(1 for r in results if (r or "").startswith(BASE[:6]))
print(f"  翻译了 {len(results)} 次：{[r[:10] for r in results]}")
print(f"  同一句只翻一次：{dup == 1}   _seq={seq_after}（期望 2：BASE 一次 + OTHER 一次）")
ok = ok and dup == 1 and seq_after == 2

eng.close()
store.close()
for p in ("G:\\日语\\_dedupe_test.json", "G:\\日语\\_dedupe_test.db"):
    try:
        Path(p).unlink()
    except OSError:
        pass
print("结论：", "去重抗抖动正常" if ok else "去重抗抖动有问题")
sys.exit(0 if ok else 1)
