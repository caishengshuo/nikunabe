"""程序入口：DPI/字体/日志初始化 + 主循环。"""
from __future__ import annotations

import io
import logging
import logging.handlers
import os
import queue
import sys
import threading
import time
import tkinter as tk
import types

from . import APP_NAME, __version__, winapi
from .paths import APP_DIR, DATA_DIR, LOG_PATH, OCR_PS1, ensure_dirs
from . import winapi
from .config import Config
from .pipeline import Engine
from .store import Store


def _setup_logging() -> None:
    ensure_dirs()
    # 控制台默认是 GBK 代码页，中文日志会变乱码；切到 UTF-8 再输出
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    try:
        fh = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception:
        pass
    if sys.stderr is not None:  # pythonw 下没有 stderr
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    # 线程里未捕获的异常：windowed exe 没有 stderr，默认的 threading 钩子什么都不显示，
    # 表现为「某个功能静默失效」（例如语法讲解线程崩了，界面上只是永远只有本地那两条）。
    def _thread_hook(args) -> None:
        try:
            logging.getLogger("galtl.thread").error(
                "线程 %s 崩溃", getattr(args.thread, "name", "?"),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
        except Exception:
            pass

    threading.excepthook = _thread_hook


def _tk_scaling(root: tk.Tk) -> None:
    # Tk 的 scaling = 每点像素数；设为 dpi/72 让字号在 150% 缩放下正常
    try:
        root.tk.call("tk", "scaling", winapi.dpi_scale() * 96.0 / 72.0)
    except Exception:
        pass


def diagnostic() -> int:
    """--check：不启动主界面，跑一遍「OCR 引擎 / 模型 / 语法库」自检，结果弹窗+写日志。"""
    _setup_logging()
    log = logging.getLogger("galtl.check")
    winapi.set_dpi_aware()
    lines: list[str] = []

    def add(msg: str) -> None:
        lines.append(msg)
        log.info("%s", msg)

    add(f"{APP_NAME} v{__version__}")
    add(f"Python {sys.version.split()[0]}  冻结打包={bool(getattr(sys, 'frozen', False))}")
    try:
        add(f"DPI 缩放 {winapi.dpi_scale():.2f}  屏幕 {winapi.virtual_screen()}")
    except Exception as exc:
        add(f"DPI 查询失败：{exc}")
    add(f"数据目录 {DATA_DIR}"
        f"（{'程序目录，便携' if DATA_DIR == APP_DIR else '程序目录不可写，已回落到用户目录'}）")
    add(f"OCR 脚本 {OCR_PS1}（存在={OCR_PS1.exists()}）")

    cfg = Config()
    engine_kind = str(cfg.get("translate_engine", "llm") or "llm").strip().lower()
    engine_kind = "ms" if engine_kind in ("ms", "microsoft", "azure") else "llm"
    if engine_kind == "ms":
        add(f"翻译引擎 微软翻译 @ {cfg.get('ms_endpoint')}"
            f"  key={'已填' if (cfg.get('ms_key') or '').strip() else '空'}"
            f"  区域={cfg.get('ms_region') or '（全球）'}")
    else:
        add(f"翻译引擎 外部 API  模型 {cfg.get('model')} @ {cfg.get('base_url')}"
            f"  key={'已填' if cfg.has_key else '空'}")

    # 1) 造一张图，走完整 OCR 链路（不依赖桌面内容）
    try:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.new("RGB", (900, 140), (255, 255, 255))
        d = ImageDraw.Draw(img)
        fnt = ImageFont.truetype("C:/Windows/Fonts/YuGothM.ttc", 56)
        d.text((30, 34), "こんな時間まで起きているなんて", font=fnt, fill=(0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        store = Store()
        eng = Engine(cfg, store, queue.Queue(maxsize=64))
        t0 = time.perf_counter()
        rows = eng.ocr().recognize_png(buf.getvalue())
        dt = (time.perf_counter() - t0) * 1000
        text = "".join(r.get("text", "") for r in rows)
        add(f"OCR 识别 {dt:.0f}ms -> {text!r}")
        ok_ocr = "起きて" in text
        add(f"OCR 自检 {'通过' if ok_ocr else '异常：没认出预期文字'}")
        info = dict(getattr(eng.ocr(), "info", {}) or {})
        add(f"OCR 引擎 lang={info.get('lang')} 可用语言={info.get('available')}")
    except Exception as exc:
        ok_ocr = False
        add(f"OCR 自检失败：{type(exc).__name__}: {exc}")
        eng = store = None

    # 2) 翻译引擎联通（按模式各测各的）
    ok_llm = None
    ok_drag = False
    if engine_kind == "ms":
        if (cfg.get("ms_key") or "").strip():
            try:
                from . import msmt

                t0 = time.perf_counter()
                out = msmt.translate(
                    "明日の朝が心配だよ。",
                    key=cfg.get("ms_key") or "",
                    region=cfg.get("ms_region") or "",
                    endpoint=cfg.get("ms_endpoint") or msmt.DEFAULT_ENDPOINT,
                    timeout=min(30, int(cfg.get("timeout", 20) or 20)),
                )
                ok_llm = bool(out)
                add(f"微软翻译自检 {(time.perf_counter() - t0) * 1000:.0f}ms -> {out!r}")
            except Exception as exc:
                ok_llm = False
                add(f"微软翻译自检失败：{type(exc).__name__}: {exc}")
        else:
            add("微软翻译自检跳过（config.json 里没有 ms_key；语法/生词仍可用本地句型库）")
    elif cfg.has_key:
        try:
            from . import llm as llm_mod

            kwargs = dict(base_url=cfg.get("base_url"), model=cfg.get("model"),
                          api_key=cfg.get("api_key"), temperature=cfg.get("temperature"),
                          max_tokens=cfg.get("max_tokens"), timeout=cfg.get("timeout"))
            t0 = time.perf_counter()
            res = llm_mod.analyze_text("明日の朝が心配だよ。", kwargs, context=[])
            dt = (time.perf_counter() - t0) * 1000
            ok_llm = bool(res.get("zh"))
            add(f"模型自检 {dt:.0f}ms -> {res.get('zh')!r} 语法{len(res.get('grammar') or [])}条")
        except Exception as exc:
            ok_llm = False
            add(f"模型自检失败：{type(exc).__name__}: {exc}")
    else:
        add("模型自检跳过（config.json 里没有 api_key，仍可用本地语法提示）")

    try:
        from .grammar import COMPILED

        add(f"语法规则 {len(COMPILED)} 条")
    except Exception as exc:
        add(f"语法库加载失败：{exc}")

    try:
        if eng is not None:
            eng.close()
        if store is not None:
            store.close()
    except Exception:
        pass

    # 3) 悬浮窗「拖不动」回归：拖完再渲染一帧，位置必须保持
    try:
        from .ui_overlay import Overlay

        backup = list(cfg.get("overlay_pos") or [0, 0])
        root = tk.Tk()
        root.withdraw()
        ov = Overlay(root, cfg)
        ov.win.deiconify()
        root.update()
        x0, y0 = int(ov.win.winfo_x()), int(ov.win.winfo_y())
        # 目标点必须先夹进屏幕：悬浮窗存在用户上次拖到的位置（可能贴着上边缘），
        # 直接「往上拖 90」会得到负 y，被夹到 0 后自检就误报「拖不动」。
        vx, vy, vsw, vsh = winapi.virtual_screen()
        tx = min(max(x0 + 100, vx), vx + vsw - 200)
        ty = min(max(y0 - 90, vy), vy + vsh - 80)
        ov._on_press(types.SimpleNamespace(x_root=x0 + 20, y_root=y0 + 8, widget=ov.text))
        ov._on_drag(types.SimpleNamespace(x_root=x0 + 20 + (tx - x0), y_root=y0 + 8 + (ty - y0),
                                         widget=ov.text))
        root.update()
        ov.show({"jp": "テスト", "zh": "测试", "grammar": [], "vocab": [],
                 "notes": "", "meta": {}})
        root.update()
        x1, y1 = int(ov.win.winfo_x()), int(ov.win.winfo_y())
        want = (tx, ty)
        ok_drag = (x1, y1) == want
        add(f"悬浮窗拖动 起点({x0},{y0}) 拖动后({x1},{y1}) 期望{want} "
            f"{'通过' if ok_drag else '失败：渲染把窗口拉回原位'}")
        ov._on_release(types.SimpleNamespace(x_root=x0 + 20 + (tx - x0),
                                            y_root=y0 + 8 + (ty - y0)))
        saved = list(cfg.get("overlay_pos") or [0, 0])
        add(f"悬浮窗位置落盘 {saved} {'通过' if saved == [x1, y1] else '失败'}")
        ov.destroy()
        root.destroy()
        cfg.set("overlay_pos", backup)
        cfg.save()
    except Exception as exc:
        ok_drag = False
        add(f"悬浮窗自检异常：{type(exc).__name__}: {exc}")

    body = "\n".join(lines)
    log.info("自检结束\n%s", body)
    out = DATA_DIR / "selfcheck.txt"
    try:
        out.write_text(body + "\n", encoding="utf-8")
        add(f"结果已写入 {out}")
    except Exception:
        pass
    if os.environ.get("GALTL_NO_OPEN") != "1":
        try:
            os.startfile(str(out))  # noqa: S606  用记事本给用户看结果，不阻塞进程
        except Exception:
            pass
    print(body)
    return 0 if (ok_ocr and ok_drag and ok_llm is not False) else 1


def main() -> int:
    _setup_logging()
    log = logging.getLogger("galtl.app")
    log.info("启动 %s v%s", APP_NAME, __version__)

    winapi.set_dpi_aware()
    cfg = Config()
    store = Store()
    # 启动时把库里「同一句台词的多种读法」并成一条：OCR 每帧读出来的样子都不一样
    # （首字被对话框边缘糊住、中段漏读），不并的话同一句会有两套译文轮流上屏。
    try:
        got = store.compact()
        if got.get("merged"):
            log.info("启动时合并了 %d 条重复读法", got["merged"])
    except Exception:
        log.debug("缓存合并不影响启动", exc_info=True)
    events: "queue.Queue" = queue.Queue(maxsize=256)
    engine = Engine(cfg, store, events)

    root = tk.Tk()
    _tk_scaling(root)

    from . import uiicon  # 窗口图标：assets/app.ico（与桌面「魔女的夜宴」同款）

    if not uiicon.apply(root):
        log.info("未找到 assets/app.ico，使用 Tk 默认图标")

    from .ui_main import MainWindow  # 延迟导入：先完成 DPI 感知

    app = MainWindow(root, cfg, store, engine, events)

    threading.Thread(target=engine.preload, name="galtl-warmup", daemon=True).start()
    if cfg.get("auto_translate"):
        engine.start_auto()

    def _boom(exc_type, exc, tb):
        log.exception("未捕获异常", exc_info=(exc_type, exc, tb))

    root.report_callback_exception = _boom
    try:
        root.mainloop()
    finally:
        engine.close()
        store.close()
        log.info("退出")
    return app is not None and 0


if __name__ == "__main__":
    raise SystemExit(main())
