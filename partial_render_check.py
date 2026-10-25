"""流式中间态的渲染自检：show_partial → show（译文）→ show（语法讲解补上）。

只建悬浮窗和一个最小的预览文本框，不启动 OCR / 引擎，几百毫秒跑完。
"""
from __future__ import annotations

import sys
import tkinter as tk

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from galtl.config import Config  # noqa: E402
from galtl.ui_overlay import Overlay  # noqa: E402
from galtl.winapi import set_dpi_aware  # noqa: E402


def main() -> int:
    set_dpi_aware()
    cfg = Config()
    cfg.load()
    root = tk.Tk()
    root.withdraw()

    ov = Overlay(root, cfg)
    ov.show_window()

    jp = "こんな時間まで起きているなんて、明日の朝が心配だよ……まったく。"
    zh = "都这个点还没睡，明天早上起不来我会担心的……真是的。"

    # 1) 流式增量：前 6 个字符
    shown = []
    for i in range(1, 7):
        ov.show_partial({"jp": jp, "zh": zh[: i * 3], "ms": i * 70})
        shown.append(zh[: i * 3] in ov.text.get("1.0", "end"))
    t1 = ov.text.get("1.0", "end").strip()
    # 1.0.13 起去掉了闪烁光标 ▍：增量直接追加在句尾，文本本身就是进度条
    ok1 = all(shown) and zh[:18] in t1
    print(f"[{'ok' if ok1 else '!!'}] 增量渲染：{t1.splitlines()[-1][:34]}")

    # 2) 语法阶段提示
    ov.show_partial({"jp": jp, "zh": zh, "stage": "grammar", "ms": 830})
    t2 = ov.text.get("1.0", "end").strip()
    ok2 = "语法解析中" in t2
    print(f"[{'ok' if ok2 else '!!'}] 语法阶段：{t2.splitlines()[-1][:34]}")

    # 3) 第一段结果（译文 + 本地句型库兜底）
    base = {
        "jp": jp, "zh": zh, "kana": "", "literal": "",
        "grammar": [{"pattern": "〜なんて", "level": "N3", "meaning": "竟然…", "form": "起きているなんて"}],
        "vocab": [], "notes": "", "_pending_grammar": True,
        "meta": {"ms": 830, "engine": "text:deepseek-chat", "first_ms": 650, "seq": 7},
    }
    ov.show(base)
    t3 = ov.text.get("1.0", "end").strip()
    ok3 = "竟然" in t3 and "语法解析中" not in t3
    print(f"[{'ok' if ok3 else '!!'}] 第一段结果：{len(t3.splitlines())} 行")

    # 4) 第二段：异步讲解到达，原地刷新
    rich = dict(base)
    rich.update({
        "kana": "こんなじかんまでおきているなんて",
        "literal": "竟然醒到这种时间",
        "grammar": [
            {"pattern": "〜まで", "level": "N5", "meaning": "到…为止", "form": "こんな時間まで",
             "explain": "表示时间终点，这里是「熬到这么晚」。"},
            {"pattern": "〜なんて", "level": "N3", "meaning": "竟然…", "form": "起きているなんて",
             "explain": "提示意外与感叹，带责怪又关心的语气。"},
        ],
        "vocab": [{"word": "心配", "reading": "しんぱい", "pos": "名/形动", "meaning": "担心"}],
        "notes": "半埋怨半关心，带独占欲。",
        "enrich": True,
    })
    rich.pop("_pending_grammar", None)
    ov.show(rich)
    t4 = ov.text.get("1.0", "end").strip()
    ok4 = "心配" in t4 and "竟然" in t4 and "语法解析中" not in t4
    print(f"[{'ok' if ok4 else '!!'}] 讲解补上：{len(t4.splitlines())} 行")

    ov.destroy()
    root.destroy()
    ok = ok1 and ok2 and ok3 and ok4
    print("结论：" + ("流式与异步刷新渲染正常" if ok else "有渲染问题"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
