"""像素级兜底：OCR 读不出字，但画面上明明「有几个点」。

为什么需要：Windows 的日文 OCR 对孤立标点几乎无能为力——galgame 里
「………」这种一整句只有省略号的台词，它一个字符都返回不了，于是用户看到的是
「程序没反应」。这里不看字，只看墨迹几何：

* 墨迹总量很少（不是整幅画面，也不是一整段文字）；
* 横向被切成 2~12 段「小块」，小块的**高度彼此接近**且**垂直居中**；
* 允许左右各有一个「高块」（「」『』这些括号）。

满足就是「一行标点」，直接当作省略号处理。
"""
from __future__ import annotations

import io

from PIL import Image

try:                       # numpy 只用来算墨迹几何；没装也不能拖垮整个程序
    import numpy as np
except Exception:          # pragma: no cover - 只在极简环境里走到
    np = None


def _load_gray(png: bytes, max_width: int = 480):
    try:
        im = Image.open(io.BytesIO(png)).convert("L")
    except Exception:
        return None
    w, h = im.size
    if w < 8 or h < 6:
        return None
    if w > max_width:
        im = im.resize((max_width, max(1, round(h * max_width / w))), Image.BILINEAR)
    return np.asarray(im, dtype=np.int16)


def analyze(png: bytes) -> dict:
    """返回 {'ok', 'dots', 'glyph_ratio', 'reason'}。glyph_ratio 是小块高度占画面高的比例。"""
    out = {"ok": False, "dots": 0, "glyph_ratio": 0.0, "reason": ""}
    if np is None:
        out["reason"] = "no-numpy"
        return out
    a = _load_gray(png)
    if a is None:
        out["reason"] = "decode"
        return out
    h, w = a.shape
    bg = int(np.median(a))
    mask = np.abs(a - bg) > 32
    ink = int(mask.sum())
    if ink < max(12, (w * h) // 2000):
        out["reason"] = "blank"
        return out
    if ink > (w * h) // 10:
        out["reason"] = "too-much-ink"
        return out

    col = mask.any(axis=0)
    runs: list[tuple[int, int, int, int]] = []
    i = 0
    while i < w:
        if not col[i]:
            i += 1
            continue
        j = i
        while j + 1 < w and col[j + 1]:
            j += 1
        ys = np.where(mask[:, i:j + 1].any(axis=1))[0]
        runs.append((i, j, int(ys[0]), int(ys[-1])))
        i = j + 1

    if not runs:
        out["reason"] = "no-run"
        return out

    tall = [r for r in runs if (r[3] - r[2] + 1) > 0.5 * h]
    flat = [r for r in runs if (r[3] - r[2] + 1) <= 0.34 * h]
    out["dots"] = len(flat)
    if len(tall) > 2 or not (2 <= len(flat) <= 12):
        out["reason"] = f"tall={len(tall)} flat={len(flat)}"
        return out
    # 括号只能出现在两端
    for r in tall:
        if r[0] > 0.22 * w and r[1] < 0.78 * w:
            out["reason"] = "tall-in-middle"
            return out

    heights = [r[3] - r[2] + 1 for r in flat]
    if max(heights) > 1.9 * min(heights):
        out["reason"] = f"flat-heights {heights}"
        return out
    centers = [(r[2] + r[3]) / 2 for r in flat]
    if (max(centers) - min(centers)) > 0.16 * h:
        out["reason"] = "flat-not-aligned"
        return out
    # 点与点之间要有空隙（实心长条是「＿」或光标）
    widths = [r[1] - r[0] + 1 for r in flat]
    if max(widths) > 2.4 * max(1, min(widths)):
        out["reason"] = f"flat-widths {widths}"
        return out

    out["ok"] = True
    out["glyph_ratio"] = float(np.median(heights)) / h
    return out


def dots_like(png: bytes) -> bool:
    return bool(analyze(png).get("ok"))
