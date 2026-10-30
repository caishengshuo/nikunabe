import sys as _sys
if _sys.stdout is None:          # pythonw 下 stdout 是 None，直接 print 会崩
    _sys.stdout = open("NUL", "w", encoding="utf-8")
if _sys.stderr is None:
    _sys.stderr = open("NUL", "w", encoding="utf-8")
"""纯标点台词的回归：本地判词、规范化、以及不花 token 的直通路径。"""
import io, queue, sys
sys.path.insert(0, r"G:\日语")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

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


print("[6] OCR 全空 + 画面是「一串点」→ 当成省略号")
from PIL import Image, ImageDraw
import galtl.inkprobe as inkprobe

img = Image.new("L", (300, 60), 18)
d = ImageDraw.Draw(img)
for i in range(6):
    d.ellipse((40 + i * 24, 36, 50 + i * 24, 46), fill=235)
buf = io.BytesIO(); img.save(buf, format="PNG")
png = buf.getvalue()
check("inkprobe 认出点", inkprobe.dots_like(png), str(inkprobe.analyze(png)))

eng2 = Engine(cfg, Store(":memory:"), queue.Queue())
eng2.cfg.set("mode", "ocr"); eng2.cfg.set("punct_fallback", True)
eng2.ocr = lambda: type("O", (), {"recognize_png": lambda self, b: []})()   # OCR 一个都不认识
eng2.effective_scale = lambda w, h, factor=None: 1.0
eng2._capture_region = lambda: (0, 0, 300, 60)
import galtl.pipeline as _pl
_pl.winapi.grab_png = lambda *a, **k: (png, 300, 60)
text, got_png, ms = eng2.grab_text()
check("空 OCR 被兜底成省略号", text == "……", repr(text))
check("字高被估出来", eng2._glyph_px > 0, str(eng2._glyph_px))
res2 = eng2.translate(text)
check("直通结果 engine=punct", (res2.get("meta") or {}).get("engine") == "punct", str(res2.get("meta")))
eng2.close()

# 反例：一张「大段文字」的图不该被兜底
img2 = Image.new("L", (300, 60), 18)
d2 = ImageDraw.Draw(img2)
for i in range(9):
    d2.rectangle((16 + i * 30, 10, 38 + i * 30, 52), fill=235)
buf2 = io.BytesIO(); img2.save(buf2, format="PNG")
eng3 = Engine(cfg, Store(":memory:"), queue.Queue())
eng3.cfg.set("mode", "ocr"); eng3.cfg.set("punct_fallback", True)
eng3.ocr = lambda: type("O", (), {"recognize_png": lambda self, b: []})()
eng3.effective_scale = lambda w, h, factor=None: 1.0
eng3._capture_region = lambda: (0, 0, 300, 60)
_pl.winapi.grab_png = lambda *a, **k: (buf2.getvalue(), 300, 60)
text3, _, _ = eng3.grab_text()
check("有字的图不当标点", text3 == "", repr(text3))
eng3.close()


print("[7] 真实截图：用户那句「………」")
from PIL import Image as _Im
_shot = r"C:\Users\21298\.dsh\attachments\v1\objects\6d\6d7677f93e0e8ce12eca687c0d055f7e247c3b853d5d3c5901a6628409bac704"
try:
    _im = _Im.open(_shot)
    _crop = _im.crop((600, 1290, 900, 1345))
    _crop = _crop.resize((_crop.width * 2, _crop.height * 2), _Im.LANCZOS)
    _buf = io.BytesIO(); _crop.save(_buf, format="PNG")
    _png = _buf.getvalue()
    check("墨迹判定为点串", inkprobe.dots_like(_png), str(inkprobe.analyze(_png)))

    eng4 = Engine(cfg, Store(":memory:"), queue.Queue())
    eng4.cfg.set("mode", "ocr"); eng4.cfg.set("punct_fallback", True)
    eng4.ocr = lambda: type("O", (), {"recognize_png": lambda self, b: []})()
    eng4.effective_scale = lambda w, h, factor=None: 1.0
    eng4._capture_region = lambda: (0, 0, 300, 55)
    _pl.winapi.grab_png = lambda *a, **k: (_png, 300, 55)
    t4, _, _ = eng4.grab_text()
    check("真实像素 → 「……」", t4 == "……", repr(t4))
    r4 = eng4.translate(t4)
    check("engine=punct", (r4.get("meta") or {}).get("engine") == "punct", str(r4.get("meta")))
    eng4.close()
except FileNotFoundError:
    print("  SKIP 找不到那张截图（换了机器就跳过）")

eng.close(); store.close()
print()
print("失败项：", fails if fails else "无")
raise SystemExit(0 if not fails else 1)
