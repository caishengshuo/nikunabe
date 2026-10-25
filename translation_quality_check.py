"""译文质量回归：把真实跑歪过的句子当成用例，钉住这些行为。

用例全部来自用户机器上的 `dist\\cache.db`（千恋＊万花实测 + 误抓的界面文字）：
- 界面文字/路径/耗时统计被当成台词 → 必须判成噪声，不许翻、不许入库；
- 模型推脱话（「我无法翻译…」）被当成译文 → 必须识别并丢弃；
- OCR 把上一句尾巴/别的窗口拼进来 → 只保留台词那几行（折行尾巴要留）；
- 「さん」这种敬称被当语法点 → 必须过滤掉。

跑法：python translation_quality_check.py（exit 0 = 全过）
"""

from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from galtl.pipeline import _filter_grammar
from galtl.textutil import (
    is_meta_reply,
    looks_like_ui_noise,
    normalize,
    pick_dialogue_line,
    strip_nameplate,
)

fails: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    if not ok:
        fails.append(f"{name}: got={got!r} want={want!r}")
    print(f"  [{'ok' if ok else 'FAIL'}] {name}: {got!r}")


# ---------- 1. 界面文字不许当台词 ----------
NOISE = [
    "イ壬勞,/凋用指令,@文件或対活",
    "laltl\\winap.i..py\nー用吋9分7秒・・・",
    "ー用吋11分57秒・・・\nイ壬勞,/凋用指令,@文件或対活",
    "・Launchappandwaitf0「vl°C.160",
    "FinaIlogandversionverification",
    "9—<9用量10.7Mtok07:50",
    "1.0.16(dist\\galgame日助手.exe已,在込彳ラ)",
    "Regressionsandrebuild1°C.15",
    # 1.0.18：末尾「・・・」（U+30FB 中点）曾被当成假名，于是整行漏过噪声判定
    "深度求索中,用吋10分46秒・・・",
    "用时 11 分 57 秒……",
    "ー用吋8分46秒・・・",
]
print("1) 界面文字 → 判噪声")
for t in NOISE:
    check(t[:26], looks_like_ui_noise(t), True)

# ---------- 2. 真台词不许误杀 ----------
CLEAN = [
    "こんな時間まで起きているなんて、明日の朝が心配だよ......",
    "「あっ、はい。なんですか?」",
    "お客さんってば」",
    "………",
    "【将臣】",
    "はい、そうです。",
    "ちょっと待って！",
    "えっ？",
    "そうですね、じゃあ行きましょうか。",
    "飛び起きた俺は、前に座っている運転手に返事をする。",
]
print("2) 正常台词 → 不算噪声")
for t in CLEAN:
    check(t[:26], looks_like_ui_noise(t), False)

# ---------- 3. 模型推脱话 ----------
print("3) 模型推脱话识别")
BAD_REPLY = [
    "这看起来不是日文台词，而是程序路径和乱码信息，我无法翻译。请提供需要翻译的日文台词。",
    "抱歉，这里没有可翻译的内容。",
    "作为AI，我无法判断这段文字的意思，请提供日文原文。",
]
for t in BAD_REPLY:
    check(t[:20], is_meta_reply(t), True)
for t in ["啊，是。什么事？", "我说客人啊", "……", "就快到了，要停在哪里呢？"]:
    check(t, is_meta_reply(t), False)

# ---------- 4. 多行拼接只留台词 ----------
print("4) 多行清洗")
check(
    "中文UI+日文台词",
    normalize(pick_dialogue_line(strip_nameplate(
        "我猛地坐了起来,逢忙了一声0\n飛ひ起きた俺は、前に座っている運転手に返事をする。"
    ))),
    "飛ひ起きた俺は、前に座っている運転手に返事をする。",
)
check(
    "折行尾巴保留",
    normalize(pick_dialogue_line(
        "こんな時間まで起きているなんて、明日の朝が心配だよ......\nまったく。"
    )),
    "こんな時間まで起きているなんて、明日の朝が心配だよ......\nまったく。",
)
check(
    "名牌+正文",
    normalize(pick_dialogue_line(strip_nameplate("【将臣】\nあっ、はい。なんですか?"))),
    "あっ、はい。なんですか?",
)

# ---------- 5. 语法校验：敬称不算语法点 ----------
print("5) 语法过滤")
items = [
    {"pattern": "さん", "form": "さん", "meaning": "先生/女士", "level": "N5"},
    {"pattern": "〜ている", "form": "起きている", "meaning": "正在…", "level": "N5"},
]
kept = _filter_grammar(items, "さん』\n起きている", set())
check("只留 〜ている", [k["pattern"] for k in kept], ["〜ている"])

print()
if fails:
    print(f"失败 {len(fails)} 项：")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("译文质量回归：全部通过")
