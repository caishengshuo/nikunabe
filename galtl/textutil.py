"""文本规范化与判定工具。"""
from __future__ import annotations

import difflib
import re
import unicodedata

_HIRA = r"\u3041-\u309f"
_KATA = r"\u30a0-\u30ff"
_KANJI = r"\u4e00-\u9fff\uf900-\ufaff"
_CJK = f"{_HIRA}{_KATA}{_KANJI}\u30fc\u3005"

_JP_RE = re.compile(f"[{_HIRA}{_KATA}{_KANJI}]")
_SPACE_RE = re.compile(r"[ \t\u3000\u00a0]+")
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u2060"), None)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def normalize(text: str) -> str:
    """NFKC + 去零宽 + 压空格。用于 OCR 输出清洗。"""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", text)
    s = s.translate(_ZERO_WIDTH)
    s = _CTRL_RE.sub("", s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _SPACE_RE.sub(" ", s)
    s = "\n".join(line.strip() for line in s.split("\n"))
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def has_japanese(text: str) -> bool:
    return bool(_JP_RE.search(text or ""))


# 有没有假名。日语台词几乎一定带假名；纯汉字多半是汉化版游戏的中文台词或系统提示，
# 拿日语模型硬翻就会「牛头不对马嘴」，所以要单独判一次。
_KANA_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff\u31f0-\u31ff]")

# 少数「纯汉字但确实是日语」的短句，白名单放行
_KANJI_ONLY_OK = {
    "無理", "了解", "大丈夫", "結構", "遠慮", "我慢", "迷惑", "元気", "平気", "本気",
    "冗談", "最低", "最悪", "最高", "普通", "当然", "確認", "注意", "危険", "禁止",
}


def has_kana(text: str) -> bool:
    return bool(_KANA_RE.search(text or ""))


def looks_japanese(text: str) -> bool:
    """真·日语台词：带假名，或是白名单里的纯汉字短句。"""
    t = (text or "").strip()
    if not t:
        return False
    if has_kana(t):
        return True
    return t.strip("。！？!?…ー、, ") in _KANJI_ONLY_OK


def jp_ratio(text: str) -> float:
    """日文字符（假名/汉字）在非空白字符中的占比，用于过滤 UI 噪声。"""
    body = [c for c in (text or "") if not c.isspace()]
    if not body:
        return 0.0
    hit = sum(1 for c in body if _JP_RE.match(c))
    return hit / len(body)


def is_meaningful(text: str, min_ratio: float = 0.35, min_chars: int = 2) -> bool:
    if len(re.sub(r"\s", "", text or "")) < min_chars:
        return False
    return jp_ratio(text) >= min_ratio


def dedupe_key(text: str) -> str:
    """去重键：忽略空白/标点/大小写差异，抹平 OCR 抖动。"""
    s = normalize(text)
    s = re.sub(r"[\s、。,.!?！？…‥・「」『』（）()\[\]【】~〜ー\-—–:：;；\"'“”‘’]", "", s)
    return s.lower()


_STABLE_KEEP = re.compile(f"[^{_HIRA}{_KATA}{_KANJI}0-9a-zA-Z\u30fc\u3005]")


def stable_key(text: str) -> str:
    """稳定性判定用的键：只留假名/汉字/字母数字。

    OCR 会把闪烁的 ▼ 箭头、光标、图标零星认成各种符号，同一句台词每帧的
    dedupe_key 都可能不同；这里把符号全部丢掉，让「同一句」真正稳定下来。
    """
    return _STABLE_KEEP.sub("", normalize(text)).lower()


def similar_key(a: str, b: str, threshold: float = 0.9) -> bool:
    """两段键是否算「同一句台词」（容忍箭头/图标被认成假名、零星错字）。

    为什么需要：stable_key 能抹掉符号，但 OCR 有时会把闪烁的 ▼ 认成「マ」「▼」之外
    的假名，于是同一句台词每帧的键都不同。键一变，`_seq` 就加一，正在流式补讲的
    语法就会被判定为「玩家已翻页」而丢弃——界面表现就是「语法只有本地那两条，
    模型讲解永远不出现」。
    """
    if not a or not b:
        return False
    if a == b:
        return True
    la, lb = len(a), len(b)
    if la < 4 or lb < 4:
        return False
    if abs(la - lb) > max(2, int(0.25 * max(la, lb))):
        return False
    if a in b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).quick_ratio() >= threshold


def display_join(lines: list[str]) -> str:
    """把 OCR 行拼成展示文本：日文换行多半是排版折断，直接连写。"""
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        out.append(ln)
    return "\n".join(out)


def one_line(text: str) -> str:
    return re.sub(r"\s*\n\s*", "", normalize(text))
