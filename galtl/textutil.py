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
    # 包含关系：同一句台词「被截断」时（打字机没打完、末尾被闪烁的 ▼ 盖住）短版
    # 会长在长版里。但「太短的片段」不足以证明是同一句（8 个字对 20 个字），
    # 所以要求短边至少 10 字、且不低于长边一半。
    if a in b or b in a:
        short = min(la, lb)
        if short >= 10 and short / max(la, lb) >= 0.5:
            return True
        if short < 10:
            return False
    if abs(la - lb) > max(2, int(0.25 * max(la, lb))):
        return False
    # 前缀覆盖率：OCR 把末尾几个字读成噪声（「ていなか」→「ていなガ0干恋万花」）时，
    # 两串前缀完全一致、只有尾部不同。字面相似度会被尾部噪声拉低，
    # 而「同一条台词」的判定更应该看开头是否一致（长度差已在上面的门里限制住）。
    n = min(la, lb)
    common = 0
    for i in range(n):
        if a[i] != b[i]:
            break
        common = i + 1
    if n >= 8 and common / n >= 0.7:
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

# ---------- 纯标点台词 ----------
# 「………」「!?」这种一整句只有标点的台词，OCR 认得出来，但过不了
# 「日文字符占比」那道门，也不该拿去花 token 翻。识别它的意义是：
# 1) 别当噪声丢掉——不然用户看到的是「程序没反应」；
# 2) 覆盖层照原样把标点印上去，画面才不会突然空一块。
_PUNCT_CHARS = set(
    "…‥‥・･.。,，、!！?？♪♬ー―─-~〜～"
    "「」『』【】〈〉《》<>（）()[]［］{}｛｝“”‘’\"'"
    ":：;；/／\\|｜*＊#＃@＃&＆%％+＋=＝^＾_＿` ＄$￥"
    "　 ‾＿⋯﹏ 、"
)

_ELLIPSIS_RE = re.compile(r"(?:\.{2,}|。{2,}|…+|‥+|・{3,}|⋯+)")
_DASH_RUN_RE = re.compile(r"[ー―─\-]{2,}")


# 会「一个字就是一整句」的假名：galgame 里角色只应一声（ん／っ／あ）或一个
# 片假名感叹。收窄到这个集合，避免把误认出来的单字噪声也当台词送翻译。
_SINGLE_KANA_OK = set("んっッツあいうえおふはへほむよやンアイウエオー")


def is_single_kana(text: str) -> bool:
    """整句只有一个假名（「ん」「っ」「あ」这类）。"""
    body = [c for c in (text or "") if not c.isspace()]
    return len(body) == 1 and body[0] in _SINGLE_KANA_OK


def is_punct_only(text: str) -> bool:
    """整句只有标点/符号/空白（没有假名、汉字、字母、数字）。"""
    body = [c for c in (text or "") if not c.isspace()]
    if not body:
        return False
    return all(c in _PUNCT_CHARS for c in body)


def canon_punct(text: str) -> str:
    """把标点收敛成中文排版习惯：省略号统一成「……」、长破折号统一成「──」。"""
    t = normalize(text)
    t = _ELLIPSIS_RE.sub("……", t)
    t = _DASH_RUN_RE.sub("──", t)
    t = re.sub(r"[ \t\u3000]+", "", t)
    return t.strip()

# ---------- 名牌 ----------
# galgame 的对话框常把说话人放在文字上方：【将臣】/（七海）。框选稍微高一点就会把它
# 一起读进来，于是「【将臣】\n………」被当成一句台词，翻译出「将臣」这种鬼东西。
_NAME_LINE_RE = re.compile(r"^[【〔\[（(]\s*[^】〕\]）)\n]{1,10}\s*[】〕\]）)]$")


def strip_nameplate(text: str) -> str:
    """去掉首行的名牌（只在后面还有正文时才去）。"""
    lines = [ln for ln in (text or "").split("\n")]
    body = [ln for ln in lines if ln.strip()]
    if len(body) < 2:
        return text
    first = body[0].strip()
    if _NAME_LINE_RE.match(first):
        # 只掐掉那一条，其余顺序保持
        dropped = False
        out = []
        for ln in lines:
            if not dropped and ln.strip() == first:
                dropped = True
                continue
            out.append(ln)
        return "\n".join(out).strip()
    return text


# ---------- 非台词内容（UI/调试文字/别的窗口） ----------
# 用户把框选区拉得偏大，或者游戏最小化后 OCR 抓到任务栏、聊天窗口、报错弹窗，
# 这些东西会被当成台词翻出来（实测缓存里出现过「任务栏、调用指令、@文件或对话」
# 「用时 11 分 57 秒……」「laltl\winapi..py」）。它们有几个稳定的特征：
# 文件路径、英文动词开头的命令名、耗时统计、@ 后面跟中文界面词。
_UI_FRAGMENTS = (
    "@文件", "调用指令", "凋用指令", "任务栏", "工具箱", "任务看板", "记忆宫殿",
    "完全权限", "深度求索", "缓存命中", "命中", "tokens", " tok ", "tok/s",
    "耗时", "用时", "指令", "文件或对话", "分支", "仓库", "构建", "回归",
)
_UI_PATH_RE = re.compile(
    r"(?:[A-Za-z]:\\|\\\\|\.(?:py|exe|log|json|yml|yaml|db|ps1|bat|md)\b|\bdist\b)",
    re.IGNORECASE,
)
_UI_TIMER_RE = re.compile(r"\d+\s*分\s*\d+\s*秒|\d+\s*:\s*\d{2}\s*$")
_UI_TIMER_TEXT_RE = re.compile(r"用[时時吋肘对對]\s*\d|耗[时時]\s*\d")
_ASCII_RE = re.compile(r"[A-Za-z]")
_KANA_ANY_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
# 真正的假名：排除「・」（U+30FB）、「゠」（U+30A0）这类标点式片假名。
# 实测「深度求索中,用吋10分46秒・・・」曾被当成台词——就因为末尾三个中点算作了假名。
_KANA_REAL_RE = re.compile(r"[\u3041-\u3096\u30a1-\u30fa\u30fc-\u30ff]")
_AT_CJK_RE = re.compile(r"[@＠]\s*[\u4e00-\u9fff]")
_SYMBOL_RE = re.compile(r"[,，/／\\|｜@＠#＃~～^`]")


def looks_like_ui_noise(text: str) -> bool:
    """判断这段文字是不是「界面上别的东西」而不是游戏台词。

    OCR 连界面文字也会读歪（「调用指令」→「凋用指令」、「@文件或对话」→「,@文件或対活」），
    所以判据不能只靠整词匹配，还要看结构：@ 后面跟汉字、时间统计、路径、符号密度。
    """
    t = normalize(text)
    if not t:
        return False
    if _UI_PATH_RE.search(t):
        return True
    low = t.lower()
    for frag in _UI_FRAGMENTS:
        if frag.lower() in low:
            return True
    if _AT_CJK_RE.search(t):
        return True
    if _UI_TIMER_RE.search(t) and len(_KANA_REAL_RE.findall(t)) <= 1:
        return True
    # 「深度求索中,用吋10分46秒・・・」：OCR 连「时」都会读成「吋/肘」，
    # 所以看「用时/耗时 + 数字」这个结构，而不是整词匹配。
    if _UI_TIMER_TEXT_RE.search(t) and len(_KANA_REAL_RE.findall(t)) <= 1:
        return True
    letters = len(_ASCII_RE.findall(t))
    kana = len(_KANA_REAL_RE.findall(t))
    symbols = len(_SYMBOL_RE.findall(t))
    # 英文占比过半、假名又几乎没有：那是命令名/日志行，不是台词
    if letters >= 6 and letters > 2 * max(kana, 1) and kana <= 2:
        return True
    if re.search(r"[A-Za-z]{8,}", t) and kana <= 1:
        return True
    # OCR 读歪的界面行：几乎没有假名，符号和汉字混在一起（「イ壬勞,/凋用…」）
    if kana <= 1 and symbols >= 2 and len(t) >= 6:
        return True
    return False


_META_REPLY_HINTS = (
    "无法翻译", "不是日文", "不是日语", "请提供", "看起来不是",
    "没有可翻译", "抱歉", "作为 AI", "作为AI", "我无法", "这段文字",
)


def is_meta_reply(zh: str) -> bool:
    """模型的推脱/解释被当成译文（实测被写进缓存，从此每次显示「我无法翻译」）。"""
    t = normalize(zh)
    if not t:
        return False
    hits = sum(1 for h in _META_REPLY_HINTS if h in t)
    if hits >= 2:
        return True
    if hits == 1 and len(t) > 24:
        return True
    return False


def pick_dialogue_line(text: str) -> str:
    """多行里挑出真正那句台词（保留换行续写，只丢明显是别的东西的行）。

    OCR 常把上一句的尾巴、任务栏、聊天窗口一起读进来，拼成
    「我猛地坐了起来…\\n飛ひ起きた俺は、前に座っている運転手に返事をする。」这种。
    但一句台词被排版折成两行时（「…心配だよ……」/「まったく。」）两行都得留。
    所以按假名数过滤：假名数不到最佳行 40% 的丢掉，其余按原顺序拼回去。
    """
    lines = [ln.strip() for ln in (text or "").replace("\r", "").split("\n") if ln.strip()]
    if len(lines) <= 1:
        return (lines[0] if lines else "").strip()

    def kana(ln: str) -> int:
        return len(_KANA_ANY_RE.findall(ln))

    best = max(kana(ln) for ln in lines)
    if best <= 0:
        return max(lines, key=len).strip()

    def keep_line(ln: str) -> bool:
        k = kana(ln)
        if k >= max(2, int(best * 0.4)):
            return True
        # 「まったく。」这种折行尾巴：短、纯日文、没有英文/路径，得留下
        if len(ln) <= 10 and k >= 1 and jp_ratio(ln) >= 0.8 and not _ASCII_RE.search(ln):
            return True
        return False

    keep = [ln for ln in lines if keep_line(ln)]
    return "\n".join(keep).strip()

