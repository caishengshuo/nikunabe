"""闪烁回归：同一句台词的「长版/短版」读数不许来回翻，旧句流式残片不许上屏。

跑法：C:\\Python314\\python.exe flicker_check.py
"""
import queue
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from galtl import pipeline, textutil  # noqa: E402

FAILS = []


def chk(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (("  " + detail) if detail else ""))
    if not cond:
        FAILS.append(name)


class Cfg:
    data = {
        "mode": "ocr", "stable_reads": 1, "settle_ms": 0, "poll_ms": 200,
        "min_ratio": 0.0, "use_cache": False, "use_probe": False,
        "grammar_async": False, "grammar_phase": False, "auto_translate": True,
        "show_jp": True, "context_lines": 2,
    }

    def get(self, k, d=None):
        return self.data.get(k, d)

    def set(self, k, v):
        self.data[k] = v

    def save(self):
        pass


class Store:
    def get(self, jp, ttl=0):
        return {}

    def put(self, *a, **k):
        pass


FULL = "まだ大雑把な目的地しか伝えていなかったのを思い出した。"
TRUNC = "まだ大雑把な目的地しか伝えていなか"          # 打字机中途（同一句的截断）
NOISY = "まだ大雑把な目的地しか伝えていなガ0干恋*万花"   # 末尾把闪烁箭头认成字


def main() -> int:
    print("=== 1. similar_key：截断不再算「新台词」 ===")
    # 注意：管线里比较的是 stable_key 归一化后的键（去标点/空白/杂符），
    # 所以这里也按键比较——直接拿原文比会把「。」「*」这类也算进相似度。
    k_full, k_trunc, k_noisy = (textutil.stable_key(x) for x in (FULL, TRUNC, NOISY))
    chk("长版 contains 短版", textutil.similar_key(k_trunc, k_full))
    chk("短版 contains 长版", textutil.similar_key(k_full, k_trunc))
    chk("带噪声尾巴也算同一句", textutil.similar_key(k_noisy, k_full))
    chk("两句不相干的台词不算同一句",
        not textutil.similar_key("おはよう、いい天気だね。", "ちょっと待ってくれないか。"))
    chk("短键不参与判定（<4 字）", not textutil.similar_key("はい", "はい。"))

    print("\n=== 2. tick：同一句反复读长读短，只翻一次 ===")
    eng = pipeline.Engine(Cfg(), Store(), queue.Queue())
    calls = []

    def fake_translate(jp, png=None):
        calls.append(jp)
        return {"jp": jp, "zh": "译文%d" % len(calls), "grammar": [], "vocab": [],
                "notes": "", "meta": {"seq": eng._seq, "ms": 1}}

    eng.translate = fake_translate
    feed = [FULL, TRUNC, NOISY, FULL, TRUNC, FULL]
    it = iter(feed)
    eng.grab_text = lambda: (next(it), b"png", 1.0)

    results = []
    for _ in feed:
        r = eng.tick()
        if r:
            results.append(r)
    chk("只翻译一次", len(calls) == 1, "实际 %d 次: %s" % (len(calls), [c[:12] for c in calls]))
    chk("上屏的只有一句", len(results) == 1, "上屏 %d 次" % len(results))

    print("\n=== 3. tick：更像的新台词照旧要翻 ===")
    calls.clear()
    feed2 = ["おはよう、いい天気だね。", "ちょっと待ってくれないか。"]
    it2 = iter(feed2)
    eng.grab_text = lambda: (next(it2), b"png", 1.0)
    out = [eng.tick() for _ in feed2]
    chk("两句都翻了", len(calls) == 2, str([c[:10] for c in calls]))
    chk("两句都上屏", sum(1 for x in out if x) == 2)

    print("\n=== 4. 界面：过期流式残片被丢掉 ===")
    shown = {"partial": [], "result": []}

    class Ov:
        def show(self, res):
            shown["result"].append(res.get("zh"))

        def show_partial(self, data):
            shown["partial"].append(data.get("zh"))

        def screen_rect(self):
            return (0, 0, 10, 10)

        def hide_window(self):
            pass

    seq_state = {"cur": 3}
    # 复刻 ui_main._pump 里的判定逻辑
    def dispatch(kind, data):
        if kind == "result":
            seq = (data.get("meta") or {}).get("seq")
            if seq is not None:
                seq_state["cur"] = int(seq)
            shown["result"].append(data.get("zh"))
        elif kind == "partial":
            seq = data.get("seq")
            if seq is not None and seq_state["cur"] and int(seq) < int(seq_state["cur"]):
                return "dropped"
            shown["partial"].append(data.get("zh"))
        return "shown"

    chk("旧 seq 的增量被丢", dispatch("partial", {"seq": 2, "zh": "上一句残片"}) == "dropped")
    chk("当前 seq 的增量放行", dispatch("partial", {"seq": 3, "zh": "本句增量"}) == "shown")
    dispatch("result", {"zh": "本句完整", "meta": {"seq": 4}})
    chk("翻页后旧句残片也被丢", dispatch("partial", {"seq": 3, "zh": "本句残片"}) == "dropped")
    chk("新句增量放行", dispatch("partial", {"seq": 4, "zh": "下一句"}) == "shown")
    chk("屏幕只有新内容", shown["partial"] == ["本句增量", "下一句"], str(shown["partial"]))

    print("\n结果：" + ("全部通过" if not FAILS else "失败 %d 项: %s" % (len(FAILS), FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
