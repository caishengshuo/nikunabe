"""字幕条流式上屏回归：验证「增量追加 + 不搬窗口 + 淡入不重排」。

跑法：C:\\Python314\\python.exe overlay_stream_check.py
"""
import os
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from galtl import ui_overlay, winapi  # noqa: E402


class Cfg:
    def __init__(self, data):
        self.data = dict(data)

    def get(self, k, d=None):
        return self.data.get(k, d)

    def set(self, k, v):
        self.data[k] = v

    def save(self):
        return None


def main() -> int:
    winapi.set_dpi_aware()
    cfg = Cfg({
        "overlay_width": 900,
        "overlay_pos": [200, 300],
        "overlay_visible": True,
        "show_jp": True,
        "show_kana": False,
        "overlay_bg_transparent": True,
        "zh_size": 20,
        "jp_size": 13,
        "gram_size": 12,
        "row_notes": True,
        "anim_enabled": True,
        "anim_ms": 160,
        "grammar_max_show": 3,
    })
    root = tk.Tk()
    root.withdraw()
    ov = ui_overlay.Overlay(root, cfg)
    ov.show_window()
    root.update()
    fails = []

    def chk(name, cond, detail=""):
        print(("  ok   " if cond else "  FAIL ") + name + (("  " + detail) if detail else ""))
        if not cond:
            fails.append(name)

    # 1) 一次完整结果先立住版面
    ov.show({"jp": "こんな時間まで起きているなんて", "zh": "都熬到这么晚了",
             "grammar": [], "vocab": [], "notes": ""})
    root.update()
    base_geom = ov._geom
    chk("首帧有几何", base_geom[0] == 900 and base_geom[1] > 0, str(base_geom))

    # 2) 流式增量：几何只在「行数真的变多」时长高，绝不逐帧抖动、也不会中途变矮
    zh = "都熬到这么晚了"
    geom_seen = []
    for piece in ["，", "明天", "早上", "可", "起", "不来", "啊", "……", "真是的。"]:
        zh += piece
        ov.show_partial({"jp": "こんな時間まで起きているなんて", "zh": zh, "stage": "translate"})
        root.update()
        geom_seen.append(ov._geom)
        time.sleep(0.01)
    heights = [h for _, h in geom_seen]
    churn = sum(1 for i in range(1, len(heights)) if heights[i] != heights[i - 1])
    grows = all(h >= heights[i - 1] for i, h in enumerate(heights) if i > 0)
    chk("高度单调不降", grows, str(heights))
    chk("增量不抖动", churn <= 2, "变化 %d 次（%d 个增量）: %s" % (churn, len(heights), heights))

    txt = ov.text.get("1.0", "end-1c")
    chk("文本等于最后一次增量", zh in txt, txt.replace("\n", " / ")[:80])
    chk("没有重复拼接", txt.count(zh) == 1, "出现 %d 次" % txt.count(zh))

    # 3) 淡入过程中不得重排（几何与文本行都不变）
    before = ov._geom
    ov._fade_zh()
    for _ in range(6):
        root.update()
        time.sleep(0.02)
    chk("淡入不动几何", ov._geom == before, "%s -> %s" % (before, ov._geom))
    chk("淡入不改正文", zh in ov.text.get("1.0", "end-1c"))

    # 4) 完整结果到来时换行、光标消失、标记就位
    ov.show({"jp": "こんな時間まで起きているなんて", "zh": zh,
             "grammar": [{"pattern": "〜なんて", "level": "N3", "meaning": "竟然…"}],
             "vocab": [{"word": "起きる", "reading": "おきる", "meaning": "起床"}],
             "notes": ""})
    root.update()
    txt2 = ov.text.get("1.0", "end-1c")
    chk("完整结果无流式光标", "▍" not in txt2, txt2.replace("\n", " / ")[:80])
    chk("中文标记存在", bool(ov.text.tag_ranges("zh")))
    chk("高度按内容增长", ov._geom[1] >= before[1], "%s -> %s" % (before, ov._geom))

    # 5) 新一句：允许变矮（完整结果路径），但不得高过屏幕
    ov.show({"jp": "あっ、はい。", "zh": "啊，是。", "grammar": [], "vocab": [], "notes": ""})
    root.update()
    vx, vy, sw, sh = winapi.virtual_screen()
    x, y, w, h = ov.screen_rect()
    chk("窗口在屏内", y + h <= vy + sh, "y=%d h=%d 屏高=%d" % (y, h, sh))

    ov.destroy()
    root.destroy()
    print("\n结果：%s" % ("全部通过" if not fails else "失败 %d 项: %s" % (len(fails), fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
