# -*- coding: utf-8 -*-
"""回归：只有一个小假名的台词（「ん」「っ」「あ」）能不能认出来并翻下去。

背景：Windows 自带的日文 OCR 对单字台词一律返回空（`probe_single_kana*.py` 有记录，
换字体/字号/黑白配色都一样）,而 galgame 里角色只应一声的台词非常常见。
现在的做法：
1. `textutil.is_single_kana` 把「一个字就是一整句」的情况从「日文字符太少」的
   噪声门里放行；
2. `glyphocr` 在 OCR 返回空时按字形模板匹配认这一个字；
3. 覆盖层字号仍按墨迹高度对齐。

本脚本不发任何网络请求。
"""

from __future__ import annotations

import io
import queue
import sys

sys.path.insert(0, r"G:\日语")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from PIL import Image, ImageDraw, ImageFont

from galtl import glyphocr
from galtl.config import Config
from galtl.pipeline import Engine
from galtl.store import Store
from galtl.textutil import is_meaningful, is_punct_only, is_single_kana

OK = "[ok]"
BAD = "[!!]"
FONT = r"C:\Windows\Fonts\YuGothM.ttc"
FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(f"{OK if cond else BAD} {msg}")
    if not cond:
        FAILS.append(msg)


def render(txt: str, size: int = 44, font: str = FONT, bg=(16, 20, 26), fg=(236, 240, 246)) -> bytes:
    f = ImageFont.truetype(font, size)
    w = int(size * (max(len(txt), 1) + 1) + 48)
    h = int(size * 2.2)
    im = Image.new("RGB", (w, h), bg)
    ImageDraw.Draw(im).text((24, 12), txt, font=f, fill=fg)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def part_textutil() -> None:
    print("\n=== 1. 单假名的判定 ===")
    check(is_single_kana("ん"), "「ん」算单假名台词")
    check(is_single_kana("っ"), "「っ」算单假名台词")
    check(is_single_kana(" あ "), "「 あ 」去空白后也算")
    check(not is_single_kana("はい"), "「はい」不是单字")
    check(not is_single_kana("…"), "标点不算单假名（走标点分支）")
    check(not is_meaningful("ん"), "旧的噪声门确实会把它判掉（说明放宽是必要的）")
    check(is_punct_only("…"), "「…」仍是纯标点")


def part_glyph() -> None:
    print("\n=== 2. 字形模板认单字 ===")
    info = glyphocr.info()
    print(f"    模板库：{info['glyphs']} 条 / {info['fonts']} 种字体")
    expect = ["ん", "っ", "あ", "ッ", "え", "ン"]
    hit = 0
    for ch in expect:
        got, conf = glyphocr.recognize(render(ch))
        hit += 1 if got == ch else 0
        print(f"    {ch!r:5} -> {got!r:5} 置信差 {conf:+.3f}")
    check(hit >= 5, f"6 个真实单字里认对 {hit} 个（允许 1 个形近误判）")

    print("\n=== 3. 不能误认：图标/图形/整段文字 ===")
    # ▼ 箭头
    im = Image.new("RGB", (120, 120), (16, 20, 26))
    ImageDraw.Draw(im).polygon([(30, 40), (90, 40), (60, 90)], fill=(236, 240, 246))
    b = io.BytesIO(); im.save(b, "PNG")
    got, conf = glyphocr.recognize(b.getvalue())
    check(got == "", f"实心三角不当假名（得到 {got!r}，置信差 {conf:+.3f}）")

    # ■ 方块
    im = Image.new("RGB", (120, 120), (16, 20, 26))
    ImageDraw.Draw(im).rectangle([40, 40, 80, 80], fill=(236, 240, 246))
    b = io.BytesIO(); im.save(b, "PNG")
    got, _ = glyphocr.recognize(b.getvalue())
    check(got == "", f"实心方块不当假名（得到 {got!r}）")

    got, _ = glyphocr.recognize(render("こんな時間まで起きているなんて"))
    check(got == "", f"整段文字不当单字（得到 {got!r}）")

    im = Image.new("RGB", (200, 100), (16, 20, 26))
    b = io.BytesIO(); im.save(b, "PNG")
    got, _ = glyphocr.recognize(b.getvalue())
    check(got == "", f"纯色画面不当单字（得到 {got!r}）")


def part_pipeline() -> None:
    print("\n=== 4. 管线：识别 → 放行 → 翻译 ===")
    cfg = Config()
    cfg.data["mode"] = "ocr"
    cfg.data["glyph_fallback"] = True
    cfg.data["region"] = [0, 0, 400, 200]
    store = Store()
    events: "queue.Queue" = queue.Queue()
    eng = Engine(cfg, store, events)

    png = render("ん")

    # OCR 装死（真机就是这样），只留字形兜底
    eng._ocr = type("FakeOcr", (), {"recognize_png": lambda self, p: [], "info": {}})()

    import galtl.winapi as winapi

    orig_grab = winapi.grab_png
    winapi.grab_png = lambda *a, **k: (png, 400, 200)          # type: ignore[assignment]
    try:
        text, got_png, ms = eng.grab_text()
    finally:
        winapi.grab_png = orig_grab                              # type: ignore[assignment]

    check(text == "ん", f"OCR 空 → 字形兜底认出 {text!r}（{ms:.0f}ms）")
    check(eng._glyph_px > 0, f"顺带量到原文字高 {eng._glyph_px:.1f}px")

    # 噪声门放行：单假名不该被「日文字符太少」拦掉
    import galtl.pipeline as pl

    orig_translate = eng.translate
    eng.translate = lambda jp, png=None: {"jp": jp, "zh": "嗯。", "kana": "", "literal": "",
                                          "grammar": [], "vocab": [], "meta": {"engine": "test", "ms": 1}}
    try:
        eng.cfg.data["region"] = [0, 0, 400, 200]
        eng.cfg.data["settle_ms"] = 0
        eng._seen = None
        eng._last_key = ""
        eng.cfg.data["stable_reads"] = 1
        winapi.grab_png = lambda *a, **k: (png, 400, 200)          # type: ignore[assignment]
        res = None
        for _ in range(8):
            eng._ocr = type("FakeOcr", (), {"recognize_png": lambda self, p: [], "info": {}})()
            res = eng.tick(force=False) or res
            if res:
                break
            import time
            time.sleep(0.05)
    finally:
        winapi.grab_png = orig_grab                               # type: ignore[assignment]
        eng.translate = orig_translate                              # type: ignore[assignment]

    check(bool(res) and (res or {}).get("jp") == "ん", f"tick 没有把它当噪声丢掉（结果 {res and res.get('jp')!r}）")
    check(bool(res) and (res or {}).get("zh") == "嗯。", "单字台词走完了翻译链路，译文上屏")


def main() -> int:
    part_textutil()
    part_glyph()
    part_pipeline()
    print()
    if FAILS:
        print(f"{BAD} {len(FAILS)} 项失败：")
        for f in FAILS:
            print("   -", f)
        return 1
    print(f"{OK} 单假名识别全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
