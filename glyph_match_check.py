import io, sys, statistics, tkinter as tk
sys.path.insert(0, r"G:\日语")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from PIL import Image
import tkinter.font as tkfont
from galtl.config import Config
from galtl import ocr
from galtl.ui_cover import CoverWindow

# 用用户那张真实截图里的台词量字高（离线、不截屏、不花钱）
shot = r"C:\Users\21298\.dsh\attachments\v1\objects\c9\c969fc956c00863f6ed76e241dc6410c22354464c5c1f1672bea3142bdb5b779"
crop = Image.open(shot).convert("L").crop((573, 1247, 573 + 1630, 1247 + 228))
cfg = Config(); cfg.load()
eng = ocr.make_ocr(dict(cfg.data))
lines = eng.recognize_png(open(r"G:\日语\promo\game_crop.png", "rb").read())
hs = [l["h"] for l in lines if l["text"].strip()]
glyph = statistics.median(hs) if hs else 0
print("OCR:", "".join(l["text"] for l in lines))
print("原文实测字高 =", glyph, "px")

root = tk.Tk(); root.withdraw()
cov = CoverWindow(root, cfg)
cov._enabled = True
cov.set_rect((573, 1247, 1630, 228))
cov.show("啊，是。什么事？", glyph)
root.update()
f = tkfont.Font(family=cov._family, size=int(cov.txt.cget("font")[1]) if not isinstance(cov.txt.cget("font"), str) else int(str(cov.txt.cget("font")).split()[-1]))
em = f.measure("漢")
print(f"覆盖层字号 = {f.cget('size')}pt → 漢 em={em}px，字面高≈{em*0.9:.1f}px，原文字高 {glyph}px")
ratio = (em * 0.9) / max(1, glyph)
print(f"比例 = {ratio:.2f}")
ok = 0.85 <= ratio <= 1.15
print("字号跟原文一致：", "通过" if ok else "失败")
# 再验一次极端值不会被 clamp 打死
for want in (14, 20, 34, 44):
    s = cov._size_for_glyph(want, 1500, 200, "测试一句中等长度的中文台词")
    ff = tkfont.Font(family=cov._family, size=s)
    print(f"  目标字面高 {want}px → {s}pt (em={ff.measure('漢')}px, 字面≈{ff.measure('漢')*0.9:.0f}px)")
cov.destroy(); root.destroy()
raise SystemExit(0 if ok else 1)
