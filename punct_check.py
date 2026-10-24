"""纯标点台词的回归：本地判词、规范化、以及不花 token 的直通路径。"""
import io, queue, sys
sys.path.insert(0, r"G:\日语")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from galtl import llm, textutil as T
from galtl.config import Config
from galtl.pipeline import Engine
from galtl.store import Store

fails = []

def check(name, cond, extra=""):
    print(("  OK   " if cond else "  FAIL ") + name + (f"   {extra}" if extra else ""))
    if not cond:
        fails.append(name)

print("[1] is_punct_only")
for text, want in [
    ("………", True), ("......", True), ("。。。", True), ("「………」", True),
    ("【将臣】", False), ("!?", True), ("……ーー", True), ("　……　", True),
    ("あっ、そう", False), ("あっ…", False), ("", False), ("   ", False),
    ("♪", True), ("2026", False),
]:
    check(f"is_punct_only({text!r}) == {want}", T.is_punct_only(text) is want)

print("[2] canon_punct")
for text, want in [
    ("......", "……"), ("。。。", "……"), ("………", "……"), ("…", "……"),
    ("「………」", "「……」"), ("!?", "!?"), ("ーーー", "──"),
]:
    got = T.canon_punct(text)
    check(f"canon_punct({text!r}) -> {got!r}", got == want)

print("[3] translate 直通（不调模型）")
cfg = Config(); cfg.load()
cfg.set("mode", "ocr"); cfg.set("use_cache", True)
store = Store(":memory:")
eng = Engine(cfg, store, queue.Queue())

def boom(*a, **k):
    raise AssertionError("纯标点不该调模型")
llm.translate_stream = boom  # 任何模型调用都会炸

res = eng.translate("「………」")
check("zh 是规范化标点", res.get("zh") == "「……」", repr(res.get("zh")))
check("engine == punct", (res.get("meta") or {}).get("engine") == "punct", str(res.get("meta")))
check("没有语法/生词", not res.get("grammar") and not res.get("vocab"))
check("ms 是数字", isinstance((res.get("meta") or {}).get("ms"), (int, float)))

print("[4] tick 端到端（OCR 结果直接喂「………」）")
seen_events = []
eng.emit = lambda kind, data=None: seen_events.append((kind, data))
eng.grab_text = lambda force=False: ("………", b"\x89PNG", 5.0) if not force else ("………", b"\x89PNG", 5.0)
eng.cfg.set("stable_reads", 1)
eng.cfg.set("settle_ms", 0)
out = eng.tick(force=True)
check("tick 有返回（不是被当噪声丢掉）", out is not None, repr(out)[:80])
check("译文是 ……", (out or {}).get("zh") == "……", repr((out or {}).get("zh")))
kinds = [k for k, _ in seen_events]
check("状态先报「翻译中」", any(k == "status" and "翻译" in str(v) for k, v in seen_events), str(kinds))
check("result 由自动循环负责 emit（tick 只返回）", "result" not in kinds)


print("[5] strip_nameplate（说话人名牌）")
for text, want in [
    ("【将臣】\n………", "………"),
    ("（七海）\nこんばんは", "こんばんは"),
    ("[司机]\n「お客さん」", "「お客さん」"),
    ("【将臣】", "【将臣】"),                      # 只有名牌、没有正文 → 不动
    ("………\n【将臣】", "………\n【将臣】"),        # 名牌不在首行 → 不动
    ("こんばんは", "こんばんは"),
]:
    got = T.strip_nameplate(text)
    check(f"strip_nameplate({text!r}) -> {got!r}", got == want)

eng.close(); store.close()
print()
print("失败项：", fails if fails else "无")
raise SystemExit(0 if not fails else 1)
