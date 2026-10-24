"""组装发布包：release/galgame-jp-helper-v<ver>-win64/ + 同名 zip。

用法：
    python pack_release.py            # 用 dist\\galgame日语助手.exe 打包
    python pack_release.py --no-zip   # 只组装目录，不压缩

产物：
    release/galgame-jp-helper-v1.0.5-win64/
        galgame日语助手.exe
        使用说明.txt      （UTF-8 BOM + CRLF，记事本直接能读）
        LICENSE
    release/galgame-jp-helper-v1.0.5-win64.zip

打包前后会做安全检查：发布目录里不允许出现 api_key 的值、cache.db、日志、config.json。
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from galtl import __version__  # noqa: E402

CJK_EXE_NAME = "galgame日语助手.exe"
# build.bat 只能写 ASCII（cmd 按 OEM 代码页解析 .bat），所以 PyInstaller 产物叫
# GalgameJP.exe；中文名在这里补上，也可以直接用已经改好名的文件。
EXE_CANDIDATES = [
    ROOT / "dist" / CJK_EXE_NAME,
    ROOT / "dist" / "GalgameJP.exe",
]
TEXT_FILES = [("使用说明.txt", "使用说明.txt"), ("LICENSE", "LICENSE")]
FORBIDDEN_NAMES = {"config.json", "cache.db", "galtl.log", "selfcheck.txt"}
SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")


def _utf8_bom_crlf(src: Path, dst: Path) -> None:
    """转成 UTF-8 BOM + CRLF，老记事本不乱码、不并成一行。"""
    text = src.read_text(encoding="utf-8")
    text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    dst.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))


def _real_key() -> str:
    cfg = ROOT / "config.json"
    if not cfg.exists():
        return ""
    try:
        return str(json.loads(cfg.read_text(encoding="utf-8")).get("api_key") or "")
    except Exception:
        return ""


def _exe() -> Path:
    for p in EXE_CANDIDATES:
        if p.exists():
            return p
    raise SystemExit(
        "找不到 exe，先跑 build.bat（应为 "
        + " 或 ".join(str(p) for p in EXE_CANDIDATES)
        + "）"
    )


def assemble() -> tuple[Path, Path | None]:
    exe = _exe()
    name = f"galgame-jp-helper-v{__version__}-win64"
    out = ROOT / "release" / name
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    shutil.copy2(exe, out / CJK_EXE_NAME)
    for src_name, dst_name in TEXT_FILES:
        src = ROOT / src_name
        if not src.exists():
            raise SystemExit(f"缺少 {src}")
        _utf8_bom_crlf(src, out / dst_name)

    # 安全检查：包内不得有任何密钥/运行期文件
    key = _real_key()
    problems: list[str] = []
    for p in out.iterdir():
        if p.name in FORBIDDEN_NAMES:
            problems.append(f"不该打包的文件：{p.name}")
            continue
        if p.suffix.lower() in {".txt", ".md", ".json", ".yml", ".yaml"}:
            body = p.read_text(encoding="utf-8", errors="ignore")
            if key and key in body:
                problems.append(f"{p.name} 里出现了真实 api_key")
            if SECRET_PATTERN.search(body):
                problems.append(f"{p.name} 里出现了疑似 key（sk-…）")
    if problems:
        raise SystemExit("安全检查未通过：\n  " + "\n  ".join(problems))

    zip_path = None
    if "--no-zip" not in sys.argv:
        # 注意：不能用 Path.with_suffix —— 目录名里的「-v1.0.5-win64」会被当成后缀切掉
        zip_path = out.parent / (out.name + ".zip")
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for p in sorted(out.iterdir()):
                zf.write(p, f"{name}/{p.name}")
    return out, zip_path


def report(out: Path, zip_path: Path | None) -> None:
    print(f"发布目录：{out}")
    for p in sorted(out.iterdir()):
        print(f"  {p.name:<24} {p.stat().st_size/1024/1024:6.2f} MB")
    if zip_path:
        with zipfile.ZipFile(zip_path) as zf:
            bad = zf.testzip()
            print(f"\n压缩包：{zip_path}  ({zip_path.stat().st_size/1024/1024:.2f} MB，"
                  f"{len(zf.namelist())} 个文件{'，CRC 全部通过' if bad is None else f'，CRC 错误：{bad}'})")
            for info in zf.infolist():
                print(f"  {info.filename:<52} {info.file_size/1024/1024:6.2f} MB")


if __name__ == "__main__":
    d, z = assemble()
    report(d, z)
