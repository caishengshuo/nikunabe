"""生成程序图标 assets/app.ico（多尺寸）。

图标取自桌面「魔女的夜宴」快捷方式指向的 Steam 图标文件（同款 Q 版魔女立绘）。
该文件缺失时退回程序自绘的「あ/文」图标。

运行：python make_icon.py
"""
from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw, ImageFont

OUT = pathlib.Path(__file__).resolve().parent / "assets"
SIZE = 256

# 桌面 魔女的夜宴.url 里 IconFile 指向的文件（Steam 客户端缓存的游戏图标）
GAME_ICO = pathlib.Path(
    r"D:\steam\steam\games\2e36dc423f2ab52a061359d334ee849163c9d55b.ico"
)
WANT = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]


def _font(size: int):
    for name in ("YuGothB.ttc", "YuGothM.ttc", "meiryob.ttc", "msgothic.ttc", "arialbd.ttf"):
        p = pathlib.Path("C:/Windows/Fonts") / name
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                continue
    return ImageFont.load_default()


def _frames_from_game_ico() -> dict[int, Image.Image]:
    """把游戏 ico 的所有帧读出来（按边长索引），并记住最大那帧用于缩放。"""
    src = Image.open(GAME_ICO)
    outs: dict[int, Image.Image] = {}
    for w, h in sorted(src.info.get("sizes", set())):
        src.size = (w, h)
        outs[min(w, h)] = src.convert("RGBA").copy()
    if not outs:
        raise RuntimeError("游戏图标里没有可用帧")
    return outs


def _game_icon() -> Image.Image:
    """组装多尺寸 ico：原帧直接用（游戏自带的小尺寸是手绘的），缺的尺寸从大图缩放。"""
    frames = _frames_from_game_ico()
    big_side = max(frames)
    big = frames[big_side]
    out = OUT / "app.ico"
    layers: list[Image.Image] = []
    for w, h in WANT:
        side = min(w, h)
        if side in frames:
            layers.append(frames[side].resize((w, h), Image.LANCZOS))
        else:
            layers.append(big.resize((w, h), Image.LANCZOS))
    big.save(out, format="ICO", sizes=[(w, h) for w, h in WANT])
    # PIL 的 ICO 写入会按 sizes 重新缩放最大帧，这里再确认一次帧数与尺寸
    chk = Image.open(out)
    print(f"已写入 {out}（帧：{sorted(chk.info.get('sizes', []))}，最大源 {big_side}px）")
    return big


def _drawn_icon() -> Image.Image:
    OUT.mkdir(exist_ok=True)
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角深色底
    d.rounded_rectangle([8, 8, SIZE - 8, SIZE - 8], radius=52, fill=(22, 27, 34, 255))
    d.rounded_rectangle([8, 8, SIZE - 8, SIZE - 8], radius=52, outline=(31, 111, 235, 255), width=6)

    # 主字：あ
    f_big = _font(150)
    d.text((SIZE / 2 - 4, SIZE / 2 - 18), "あ", font=f_big, fill=(240, 246, 252, 255), anchor="mm")

    # 右下角小箭头 + 蓝点（表示「翻成中文」）
    f_arrow = _font(64)
    d.text((SIZE - 56, SIZE - 52), "文", font=f_arrow, fill=(90, 170, 255, 255), anchor="mm")

    out = OUT / "app.ico"
    img.save(out, format="ICO", sizes=WANT)
    print(f"已写入 {out}（自绘兜底）")
    return img


def main() -> None:
    OUT.mkdir(exist_ok=True)
    if GAME_ICO.exists():
        try:
            img = _game_icon()
        except Exception as exc:  # 文件在但读不出来，别卡住打包
            print(f"读取游戏图标失败（{type(exc).__name__}: {exc}），改用自绘图标")
            img = _drawn_icon()
    else:
        print(f"未找到 {GAME_ICO}，改用自绘图标")
        img = _drawn_icon()
    img.resize((256, 256), Image.LANCZOS).save(OUT / "app_preview.png")
    print(f"预览：{OUT / 'app_preview.png'}")


if __name__ == "__main__":
    main()
