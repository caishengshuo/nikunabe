"""实测「第二段讲解」两条链路的首字延迟：整段 JSON vs 流式 JSONL。

用法：C:\\Python314\\python.exe enrich_stream_check.py
会各发一次真实请求（消耗两次额度），打印每一行到达的时刻。
"""
from __future__ import annotations

import sys
import time

# 控制台默认 GBK，遇到「〜」会抛 UnicodeEncodeError，直接顶掉实测
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from galtl import llm
from galtl.config import Config
from galtl import store as store_mod

JP = "べ、別にアンタのことなんか待ってないんだからねっ！"
ZH = "才、才不是在等你呢！"


def main() -> int:
    cfg = Config()
    cfg.load()
    data = cfg.data
    base_url = str(data.get("base_url") or "").strip()
    model = str(data.get("model") or "").strip()
    api_key = str(data.get("api_key") or "").strip()
    if not api_key:
        print("config 里没有 api_key，跳过")
        return 1
    kwargs = {
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
        "temperature": float(data.get("temperature", 0.3) or 0.3),
        "timeout": float(data.get("timeout", 60) or 60),
        "extra_headers": None,
        "json_mode": False,
    }
    ctx = ["こんな時間まで起きているなんて、明日の朝が心配だよ……まったく。"]
    gl = ""

    print("=== A. 旧链路：整段 JSON（必须等全部生成完）===")
    t0 = time.perf_counter()
    detail = llm.analyze_grammar(JP, ZH, kwargs, context=ctx, glossary=gl, max_tokens=520)
    t_a = (time.perf_counter() - t0) * 1000
    print(f"  整段返回 {t_a:.0f}ms → 语法 {len(detail.get('grammar') or [])} 条 / 生词 {len(detail.get('vocab') or [])} 个")
    for g in (detail.get("grammar") or [])[:3]:
        print(f"    · {g.get('pattern')} {g.get('level')} {g.get('meaning')}")

    print()
    print("=== B. 新链路：流式 JSONL（逐条到达）===")
    t0 = time.perf_counter()
    marks: dict[str, float] = {}
    gs: list[dict] = []
    vs: list[dict] = []
    meta: dict = {}
    for kind, item in llm.analyze_grammar_stream(JP, ZH, kwargs, context=ctx, glossary=gl, max_tokens=520):
        ms = (time.perf_counter() - t0) * 1000
        if kind == "meta":
            meta = item
            marks.setdefault("meta", ms)
            print(f"  {ms:7.0f}ms  [读音/语气] kana={item.get('kana','')[:18]}")
        elif kind == "g":
            if not gs:
                marks.setdefault("first_g", ms)
            gs.append(item)
            print(f"  {ms:7.0f}ms  [语法] {item.get('pattern')} {item.get('level')} {item.get('meaning','')[:24]}")
        elif kind == "v":
            if not vs:
                marks.setdefault("first_v", ms)
            vs.append(item)
            print(f"  {ms:7.0f}ms  [生词] {item.get('word')}（{item.get('reading','')}）{item.get('meaning','')[:18]}")
    t_total = (time.perf_counter() - t0) * 1000

    print()
    print("=== 结论 ===")
    print(f"  旧链路：整段 {t_a:.0f}ms 才一次性看到语法/生词")
    for k, label in (("meta", "读音与语气"), ("first_g", "第一条语法"), ("first_v", "第一个生词")):
        if k in marks:
            print(f"  新链路：{label} {marks[k]:.0f}ms（比旧链路快 {t_a - marks[k]:.0f}ms）")
    print(f"  新链路：全部收齐 {t_total:.0f}ms，共语法 {len(gs)} 条 / 生词 {len(vs)} 个")

    assert gs or vs, "流式链路一条都没解析出来"
    assert marks.get("first_g", 1e9) < t_a, "首条语法并不比整段快，说明流式没生效"
    print("  OK：流式生效，首条内容显著早于整段返回")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
