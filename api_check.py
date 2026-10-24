"""真实接口联通测试：用 config.json 里的 key 翻一句 galgame 台词，打印完整解析。

换 key / 换模型 / 换 base_url 之后跑这个确认链路通：
    python api_check.py
"""
import queue
import time

from galtl import llm
from galtl.config import Config
from galtl.pipeline import Engine
from galtl.store import Store

LINE = "こんな時間まで起きているなんて、明日の朝が心配だよ……まったく。"


def main() -> int:
    cfg = Config()
    key = cfg.get("api_key") or ""
    print("base_url =", cfg.get("base_url"), "| model =", cfg.get("model"),
          "| has_key =", cfg.has_key)
    if not cfg.has_key:
        print("config.json 里没有 api_key，先填好再跑。")
        return 1

    kwargs = dict(
        base_url=cfg.get("base_url"),
        model=cfg.get("model"),
        api_key=key,
        temperature=cfg.get("temperature"),
        max_tokens=cfg.get("max_tokens"),
        timeout=cfg.get("timeout"),
    )

    t0 = time.time()
    try:
        res = llm.analyze_text(LINE, kwargs, context=[], glossary=cfg.get("glossary") or "")
    except Exception as exc:
        print("\n调用失败：", type(exc).__name__, exc)
        return 1
    dt = (time.time() - t0) * 1000

    print(f"\n── 原文 ──\n{LINE}")
    print(f"\n── 译文 ──\n{res.get('zh')}")
    print(f"\n── 直译 ──\n{res.get('literal')}")
    print("\n── 语法 ──")
    for g in res.get("grammar") or []:
        print(f"  · {g.get('pattern')}  {g.get('level', '')}  {g.get('meaning', '')}")
        if g.get("form"):
            print(f"      片段：{g['form']}")
        if g.get("explain"):
            print(f"      {g['explain']}")
    print("\n── 生词 ──")
    for v in res.get("vocab") or []:
        print(f"  · {v.get('word')}（{v.get('reading', '')}） {v.get('meaning', '')}")
    print("\n── 语气 ──\n" + str(res.get("notes")))
    print(f"\n单次耗时 {dt:.0f}ms")

    events: "queue.Queue" = queue.Queue()
    store = Store()
    eng = Engine(cfg, store, events)
    t = time.time()
    r1 = eng.translate(LINE, None)
    t1 = (time.time() - t) * 1000
    t = time.time()
    r2 = eng.translate(LINE, None)
    t2 = (time.time() - t) * 1000
    eng.close()
    store.close()
    print("\n── 引擎 / 缓存 ──")
    print(f"首次 {t1:.0f}ms  engine={(r1.get('meta') or {}).get('engine')}  "
          f"cached={(r1.get('meta') or {}).get('cached')}")
    print(f"二次 {t2:.0f}ms  engine={(r2.get('meta') or {}).get('engine')}  "
          f"cached={(r2.get('meta') or {}).get('cached')}")
    print("\n联通性：", "通过" if res.get("zh") else "失败（无译文）")
    return 0 if res.get("zh") else 1


if __name__ == "__main__":
    raise SystemExit(main())
