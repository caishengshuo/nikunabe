"""模型输出解析回归：截断 / 代码块 / 尾逗号 / 崩坏，都必须能给出可用结果。"""
from __future__ import annotations

from galtl.llm import LlmError, parse_json

CASES = {
    "截断在字符串中": '{"noise": false, "jp": "そんなに慌てなくて',
    "截断在数组里": '{"noise": false, "jp": "あ", "zh": "啊", "grammar": [{"pattern": "〜て", "level": "N5"',
    "代码块+前后废话": '好的，结果如下：\n```json\n{"noise": false, "jp": "テスト", "zh": "测试"}\n```\n以上。',
    "尾逗号": '{"jp": "あ", "zh": "啊",}',
    "换行在字符串里": '{"jp": "あ\nい", "zh": "啊"}',
    "中文引号": '{"jp": "あ", “zh”: “啊”}',
    "不完整尾部垃圾": '{"noise": false, "jp": "あいう", "zh": "啊" garbage',
}


def main() -> int:
    bad = 0
    for name, raw in CASES.items():
        try:
            obj = parse_json(raw)
            jp, zh = obj.get("jp"), obj.get("zh")
            ok = bool(jp or zh)
            print(f"[{'ok  ' if ok else 'FAIL'}] {name}: jp={jp!r} zh={zh!r}")
            bad += 0 if ok else 1
        except LlmError as exc:
            print(f"[FAIL] {name}: {exc}")
            bad += 1

    try:
        parse_json("完全不是 JSON")
        print("[FAIL] 非 JSON 输入应当报错，却返回了结果")
        bad += 1
    except LlmError:
        print("[ok  ] 非 JSON 输入正确报错")

    print("结论：", "全部通过" if bad == 0 else f"{bad} 项失败")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
