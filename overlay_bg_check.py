import io, sys, time, tkinter as tk
sys.path.insert(0, r"G:/日语")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from galtl.config import Config
from galtl.ui_overlay import Overlay
from galtl import winapi

root = tk.Tk(); root.withdraw()
cfg = Config(); cfg.load()
cfg.set("overlay_bg_transparent", True)
ov = Overlay(root, cfg)
ov.show_window()
ov.show({"jp": "お客さんってば、こんな時間まで起きて",
         "zh": "我说客人啊，都这个点还醒着",
         "grammar": [{"pattern": "〜ってば", "level": "N3", "meaning": "我说…啊"}],
         "vocab": [{"word": "お客さん", "reading": "おきゃくさん", "meaning": "客人"}],
         "notes": ""})
for _ in range(40):
    root.update(); time.sleep(0.02)

key = ov._key_bg
tc = ov.win.attributes("-transparentcolor")
alpha = ov.win.attributes("-alpha")
print("透明色 =", repr(tc), " alpha =", alpha, " 文字控件底色 =", ov.text.cget("bg"))
ok1 = str(tc).lower() == key.lower() and ov.text.cget("bg") == key and float(alpha) == 1.0

rect = ov.screen_rect()
print("悬浮窗矩形:", rect)
png, w, h = winapi.grab_png(rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1], scale=1.0)
open(r"C:/Users/21298/AppData/Local/Temp/overlay_transparent.png", "wb").write(png)

ov.toggle_transparent()
for _ in range(20):
    root.update(); time.sleep(0.02)
tc2 = ov.win.attributes("-transparentcolor")
alpha2 = ov.win.attributes("-alpha")
print("切回后: 透明色 =", repr(tc2), " alpha =", alpha2, " 底色 =", ov.text.cget("bg"))
ok2 = (not tc2) and abs(float(alpha2) - float(cfg.get("overlay_alpha", 0.93))) < 1e-6

ov.toggle_transparent()
for _ in range(10):
    root.update(); time.sleep(0.02)
ok3 = str(ov.win.attributes("-transparentcolor")).lower() == key.lower()
print("透明可用:", ok1, "| 可切回不透明:", ok2, "| 可再切回透明:", ok3)
ov.destroy(); root.destroy()
raise SystemExit(0 if (ok1 and ok2 and ok3) else 1)

