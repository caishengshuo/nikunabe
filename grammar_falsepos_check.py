"""回归：口语回声（ってば／ったら）不能被讲成「〜ば」「〜ては」，模型瞎报的语法点也要被过滤掉。

为什么单独做一个脚本：用户实测「お客さんってば」被翻成「客人您这是」、语法讲的是「〜ては
（…的话就…）」，牛头不对马嘴。这三处（本地匹配、模型输出校验、提示词）都得守住。
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from galtl import grammar  # noqa: E402
from galtl.pipeline import _filter_grammar, _grammar_core  # noqa: E402

FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global FAIL
    print(("  ok   " if ok else "  FAIL ") + name + (("  " + detail) if detail and not ok else ""))
    if not ok:
        FAIL += 1


print("[1] 本地句型库")
hits = [h["pattern"] for h in grammar.match("お客さんってば")]
check("ってば 命中回声句型", any("ってば" in h for h in hits), f"hits={hits}")
check("不再误报 〜ば", not any(h.strip("〜") == "ば" for h in hits), f"hits={hits}")
check("不再误报 〜ては", not any("ては" in h for h in hits), f"hits={hits}")

hits = [h["pattern"] for h in grammar.match("食べてばかりいる")]
check("てば 不再被当成假定形", not any(h.strip("〜") == "ば" for h in hits), f"hits={hits}")

hits = [h["pattern"] for h in grammar.match("行けば分かる")]
check("真正的假定形仍然命中", any(h.strip("〜") == "ば" for h in hits), f"hits={hits}")

hits = [h["pattern"] for h in grammar.match("ここにいては危ない")]
check("真正的 〜ては 仍然命中", any("ては" in h for h in hits), f"hits={hits}")

hits = [h["pattern"] for h in grammar.match("飲んではいけない")]
check("禁止形归到 〜てはいけない", hits and "いけない" in hits[0], f"hits={hits}")

print("[2] 模型输出校验")
check("展示名还原", _grammar_core("〜ては（〜ては？）") == "ては", _grammar_core("〜ては（〜ては？）"))
kept = _filter_grammar(
    [
        {"pattern": "〜ては", "form": "ては"},
        {"pattern": "お客さん", "form": "お客さん"},
        {"pattern": "〜ってば／〜ったら", "form": "お客さんってば"},
    ],
    "お客さんってば",
    {"お客さん"},
)
pats = [k["pattern"] for k in kept]
check("丢掉原句里没有的 〜ては", "〜ては" not in pats, f"kept={pats}")
check("丢掉被当成语法的单词", "お客さん" not in pats, f"kept={pats}")
check("保留真实命中的 ってば", any("ってば" in p for p in pats), f"kept={pats}")

kept = _filter_grammar([{"pattern": "〜てしまう", "form": "ちゃった"}], "もう食べちゃった", set())
check("活用改写仍然保留", len(kept) == 1, f"kept={kept}")

print()
if FAIL:
    print(f"FAILED: {FAIL}")
    raise SystemExit(1)
print("全部通过")
