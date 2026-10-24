# -*- coding: utf-8 -*-
"""CI/本地都能跑的自检：不碰 OCR、不联网、不写用户配置。

检查四件事：
1. 所有核心模块能导入（含 tkinter 系 UI，CI 上最容易缺的就是它）；
2. `galtl.grammar` 的本地语法库真的能命中一句 galgame 台词；
3. `galtl.vocab` 的生词速览真的能命中；
4. `Config` 的默认值与落盘键齐全，且 `ocr_lang` 默认是 ja。

用法： python ci_selfcheck.py
"""
from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    import galtl
    import galtl.app  # noqa: F401  （会带上 tkinter、winapi）
    import galtl.pipeline  # noqa: F401
    import galtl.grammar as G
    import galtl.vocab as V
    from galtl.config import Config, DEFAULTS
    from galtl.textutil import similar_key

    print(f"python {sys.version.split()[0]}")
    print(f"version {galtl.__version__}  rules {len(G.COMPILED)}  words {len(V.WORDS)}")

    sample = "こんな時間まで起きているなんて、明日の朝が心配だよ……まったく。"
    hits = G.match(sample, 6)
    names = [h["pattern"] for h in hits]
    print("grammar hits:", names)
    if not hits:
        raise SystemExit("本地语法库没有命中任何条目")

    words = V.quick(sample, 4)
    print("vocab:", [w["word"] for w in words])
    if not words:
        raise SystemExit("生词速览没有命中任何词")

    cfg = Config("ci-selfcheck-config.json")  # 只读默认值：文件不存在就不写盘
    missing = [k for k in ("ocr_lang", "region", "poll_ms", "model", "overlay_pos") if k not in cfg.data]
    if missing:
        raise SystemExit(f"配置缺键: {missing}")
    if cfg.data["ocr_lang"] != "ja":
        raise SystemExit(f"ocr_lang 默认值不对: {cfg.data['ocr_lang']!r}")
    unknown = [k for k in cfg.data if k not in DEFAULTS]
    if unknown:
        raise SystemExit(f"配置多出未知键: {unknown}")

    if not similar_key("こんな時間まで起きているなんて", "こんな時間まで起きているなんて、", 0.9):
        raise SystemExit("similar_key 对同一句子的判重失败")

    print(f"defaults {len(DEFAULTS)} keys, model={cfg.data['model']}, ocr_lang={cfg.data['ocr_lang']}")
    print("自检通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
