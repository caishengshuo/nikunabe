# -*- coding: utf-8 -*-
"""单字兜底：Windows 自带的日文 OCR 认不出「一整句只有一个假名」。

实测（`probe_single_kana.py` / `probe_single_kana2.py`）：`ん`、`っ`、`あ` 这种
单字台词，无论换字体、换字号（28~110px）、换黑底白字还是白底黑字，Windows.Media.Ocr
一律返回空；连 `えっ` 这种两字小句也认不出，而 `はい`、`うん`、`はい。` 正常。
也就是说这不是我们预处理的问题，是引擎的硬限制。

这里的办法不碰 OCR：把画面裁到唯一那一团墨迹，归一化成 64×64 的灰度模板，
跟「系统字体渲染出来的假名表」逐一比对，取最像的那个。
模板库覆盖 5 种常见字体（黑体/明朝/圆体），所以游戏用别的字体也大致对得上。

只在一个字确实会被单独成句时才用，候选集也刻意收窄：
去掉了 ぁぃぅぇぉゃゅょ 这些永远不会单独出现的同形小字（它们在匹配时只会
把「あ」误判成「ぁ」）。
"""
from __future__ import annotations

import io
import os

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:                      # pragma: no cover - 打包里必然有 Pillow
    Image = None                       # type: ignore[assignment]

SIZE = 64
FONTS = (
    r"C:\Windows\Fonts\msgothic.ttc",
    r"C:\Windows\Fonts\YuGothM.ttc",
    r"C:\Windows\Fonts\YuGothB.ttc",
    r"C:\Windows\Fonts\meiryo.ttc",
    r"C:\Windows\Fonts\yumin.ttf",
)
# 「一个假名就是一整句」在 galgame 里真实出现过的，基本都在这里的范围
CANDIDATES = "んっッツあえうおふはへむよやアイウエオーンーっ"

_atlas: list[tuple[str, list[float]]] | None = None
_info = {"fonts": 0, "glyphs": 0}


def _bitmap(ch: str, font_path: str, px: int = 96) -> list[float] | None:
    try:
        font = ImageFont.truetype(font_path, px)
    except Exception:
        return None
    im = Image.new("L", (px * 2, px * 2), 0)
    try:
        ImageDraw.Draw(im).text((px // 2, px // 2), ch, font=font, fill=255)
    except Exception:
        return None
    box = im.getbbox()
    if not box:
        return None
    small = im.crop(box).resize((SIZE, SIZE), Image.LANCZOS)
    data = list(small.getdata())
    total = sum(data) or 1
    return [v / total for v in data]


def build_atlas() -> list[tuple[str, list[float]]]:
    """模板库（首次调用会渲染 5 种字体，约 0.2s，之后走缓存）。"""
    global _atlas
    if _atlas is not None:
        return _atlas
    atlas: list[tuple[str, list[float]]] = []
    used = 0
    for path in FONTS:
        if not os.path.exists(path):
            continue
        used += 1
        for ch in dict.fromkeys(CANDIDATES):
            bm = _bitmap(ch, path)
            if bm:
                atlas.append((ch, bm))
    _atlas = atlas
    _info["fonts"] = used
    _info["glyphs"] = len(atlas)
    return atlas


def info() -> dict:
    build_atlas()
    return dict(_info)


def _crop_single_glyph(png: bytes):
    """把图裁到唯一那团墨迹；不像「一个字」就返回 None。"""
    if Image is None:
        return None
    try:
        im = Image.open(io.BytesIO(png)).convert("L")
    except Exception:
        return None
    w, h = im.size
    if w < 6 or h < 6:
        return None
    px = im.load()
    # 背景色取边界一圈的中位数，比取全图更稳（对话框底色常带渐变）
    edge = [px[x, 0] for x in range(0, w, 3)] + [px[x, h - 1] for x in range(0, w, 3)]
    edge += [px[0, y] for y in range(0, h, 3)] + [px[w - 1, y] for y in range(0, h, 3)]
    edge.sort()
    bg = edge[len(edge) // 2] if edge else 0
    thr = 32
    # 文字可能比底色亮，也可能比底色暗
    mask_light = im.point(lambda v: 255 if v - bg > thr else 0)
    mask_dark = im.point(lambda v: 255 if bg - v > thr else 0)
    ink = mask_light.getbbox()
    if not ink or (ink[2] - ink[0]) * (ink[3] - ink[1]) < 16:
        ink = mask_dark.getbbox()
    if not ink:
        return None
    bw, bh = ink[2] - ink[0], ink[3] - ink[1]
    if bw < 5 or bh < 5:
        return None
    # 单个字：宽高比不能太扁/太窄，占画面的比例也要像一个字
    if not (0.35 <= bw / max(1, bh) <= 1.9):
        return None
    if bw * bh > 0.55 * w * h:
        return None
    crop = im.crop(ink)
    # 「实心块」不是字：正方形/方框的墨迹会填满自己的外接框（实测会把 ■ 认成「ー」）。
    # 真字的一笔一画之间有大量空白，覆盖率通常 0.15~0.40。
    probe = crop.point(lambda v: 255 if abs(v - bg) > thr else 0)
    filled = sum(1 for v in probe.getdata() if v) / max(1, probe.width * probe.height)
    if filled > 0.62:
        return None
    # 一个字只有一个墨迹块：按列投影，应该只有一段连续非空
    col_has = [(max(crop.crop((x, 0, x + 1, crop.height)).getdata()) > bg + thr) for x in range(crop.width)]
    runs = 0
    prev = False
    for c in col_has:
        if c and not prev:
            runs += 1
        prev = c
    if runs > 3:                        # 「こ」「た」这种笔画会分开，留点余量
        return None
    data = list(crop.resize((SIZE, SIZE), Image.LANCZOS).getdata())
    total = sum(data) or 1
    return [v / total for v in data], bh, w, h


def recognize(png: bytes, min_margin: float = 0.012, max_dist: float = 0.70) -> tuple[str, float]:
    """认一个孤零零的假名。返回 (字, 置信差)；认不准就返回 ("", 0.0)。"""
    got = _crop_single_glyph(png)
    if not got:
        return "", 0.0
    bm, bh, w, h = got
    atlas = build_atlas()
    if not atlas:
        return "", 0.0
    scored = sorted((sum(abs(a - b) for a, b in zip(bm, tpl)), ch) for ch, tpl in atlas)
    best_d, best_ch = scored[0]
    second_d = next((d for d, ch in scored if ch != best_ch), 1.0)
    margin = second_d - best_d
    if best_d > max_dist or margin < min_margin:
        return "", max(0.0, margin)
    return best_ch, margin


def glyph_height_px(png: bytes) -> float:
    """墨迹框高度（屏幕像素），用来给覆盖层对齐字号。"""
    got = _crop_single_glyph(png)
    return float(got[1]) if got else 0.0
