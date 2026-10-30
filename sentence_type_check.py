"""句式一致性回归：陈述句不许长出问号，疑问句不许丢问号。

背景：用户截图里游戏是「ここは京都。」（陈述），工具却显示「这里是京都吗？」。
模型不是确定性的，所以除了提示词，还要有一道机械兜底 —— 这个脚本就是钉住它的行为。

跑法：C:\\Python314\\python.exe sentence_type_check.py   （全程离线，不花 token）
"""
import io
import sys

sys.path.insert(0, r"G:\日语")
from galtl.textutil import fix_sentence_type, is_question_jp  # noqa: E402

OUT = io.open(r"G:\日语\sentence_type_check.log", "w", encoding="utf-8")
fails = []


def check(name, got, want):
    ok = got == want
    OUT.write(("[OK] " if ok else "[FAIL] ") + f"{name}: got={got!r} want={want!r}\n")
    if not ok:
        fails.append(name)


def case(name, jp, zh, want):
    check(name, fix_sentence_type(jp, zh), want)


# ---- 1. 用户报的那句：陈述 → 不许出现「吗」 ----
case("京都-陈述-带吗", "ここは京都。", "这里是京都吗？", "这里是京都。")
case("京都-陈述-吗无语号", "ここは京都。", "这里是京都吗", "这里是京都。")
case("京都-陈述-只有问号", "ここは京都。", "这里是京都？", "这里是京都。")
case("京都-陈述-正常", "ここは京都。", "这里是京都。", "这里是京都。")

# ---- 2. 真疑问句原样保留（本工具只做减法：不补问号、不改写） ----
case("京都-疑问-か", "ここは京都か。", "这里是京都吗？", "这里是京都吗？")
case("京都-疑问-全角问号", "ここは京都ですか？", "这里是京都吗？", "这里是京都吗？")
case("京都-疑问-かな", "ここは京都かな。", "这里是京都吧。", "这里是京都吧。")
case("京都-疑问-の省略问号", "行くの", "去吗？", "去吗？")
case("截断行不补问号", "まだ目的地しか伝えていなか", "只说了个大概的目的地", "只说了个大概的目的地")

# ---- 3. 中文自带疑问词的真问句：日文没 か 也不许被改成陈述 ----
case("为什么", "なぜ行く", "为什么去？", "为什么去？")
case("怎么办", "どうする", "怎么办？", "怎么办？")
case("谁", "誰だ", "是谁？", "是谁？")

# ---- 4. 不动无关内容 ----
case("普通陈述", "京都はいいところだ。", "京都是个好地方。", "京都是个好地方。")
case("省略号结尾", "……そうか。", "……这样啊。", "……这样啊。")
case("反问-是吗", "……そうか。", "……是吗。", "……是吗。")
case("反问-不是吗", "そうだな。", "难道不是吗？", "难道不是吗？")
case("感叹句", "すごい！", "好厉害！", "好厉害！")
case("空串", "", "", "")
case("中文空", "ここは京都。", "", "")
case("只有标点", "………", "………", "………")
case("引号收尾", "「行くぞ」", "「走了哦」", "「走了哦」")

# ---- 5. 幂等：同一句跑两遍结果不变 ----
first = fix_sentence_type("ここは京都。", "这里是京都吗？")
check("幂等", fix_sentence_type("ここは京都。", first), first)

# ---- 6. is_question_jp 的判据 ----
for jp, want in [
    ("ここは京都。", False),
    ("ここは京都か。", True),
    ("ここは京都ですか？", True),
    ("行くの", True),
    ("京都はいいところだ。", False),
    ("すごい！", False),
    ("……そうか。", False),
]:
    check(f"is_question_jp({jp})", is_question_jp(jp), want)

OUT.write(f"\n共 {len(fails)} 项失败\n")
OUT.close()
print("FAILS", len(fails))
sys.exit(1 if fails else 0)
