"""抓取-识别-翻译 管线。跑在后台线程，通过队列把事件抛给 UI。

事件格式：(kind, data)
  ("status", str)          一行状态文字
  ("ocr", dict)            OCR 中间结果 {"jp", "lines", "ms"}
  ("result", dict)         最终结果（llm schema + meta）
  ("error", str)           错误信息（已在内部记日志）
  ("log", str)             调试日志
"""
from __future__ import annotations

import hashlib
import logging
import queue
import re
import threading
import time

from . import grammar as grammar_mod
from . import inkprobe, llm, msmt, vocab as vocab_mod, winapi
from .config import Config
from .ocr import OcrUnavailable, make_ocr, merge_lines
from .store import Store
from .textutil import (
    canon_punct, dedupe_key, display_join, has_japanese, is_meta_reply, is_meaningful,
    is_punct_only, looks_japanese, looks_like_ui_noise, normalize, pick_dialogue_line,
    similar_key, strip_nameplate,
    stable_key,
)

log = logging.getLogger("galtl.pipeline")


def _norm_pattern(s: str) -> str:
    """句型去重用的键：波浪号/空格写法各家模型不一致，先把它们抹平。"""
    return (s or "").replace("〜", "").replace("～", "").replace(" ", "").replace("　", "").strip()


# 敬称/后缀：出现在乱码里会被当语法点（实测「さん』」→「语法：さん」），一律不算语法。
_NOT_GRAMMAR_CORES = {"さん", "ちゃん", "くん", "君", "氏", "様", "さま", "殿", "どの"}


def _grammar_core(pattern: str) -> str:
    """把「〜ては（〜ては？）」这类展示名还原成可在原句里查的核心字串。"""
    s = (pattern or "").replace("〜", "").replace("～", "").replace("~", "")
    s = re.sub(r"[（(][^）)]*[）)]", "", s)     # 去掉括号里的补充说明
    return s.strip()


def _gkey(pattern: str) -> str:
    """语法点去重键：只取第一种写法（「〜ってば／〜ったら」→ ってば），好让模型条目压住本地占位。"""
    core = _grammar_core(pattern)
    for sep in ("／", "/", "、"):
        core = core.split(sep)[0]
    return _norm_pattern(core)


def _filter_grammar(items: list[dict], jp_text: str, vocab_words: set[str]) -> list[dict]:
    """丢掉「原句里根本没有」的语法点——模型偶尔会把「ってば」讲成「〜ては」「〜ば」。

    判断依据只有两条硬事实：摘出的 form / 句型核心能不能在原句里找到；纯名词是不是
    已经作为生词列出（那就不该再当语法点讲一遍）。
    """
    out: list[dict] = []
    for it in items:
        core = _grammar_core(it.get("pattern") or "")
        form = str(it.get("form") or "").strip()
        probe = form or core
        if not probe:
            continue
        # 「さん」「ちゃん」这类是敬称后缀，不是语法点。早先它们在乱码
        # （「さん』」）上命中过，界面上就出现「语法：さん」。
        if core in _NOT_GRAMMAR_CORES:
            continue
        if len(core) < 2 and len(form) < 2:
            continue
        if core and core in vocab_words and len(core) <= 10:
            continue                            # 单词被当成语法点
        if probe in jp_text or (core and core in jp_text):
            out.append(it)
            continue
        tail = core[-2:] if len(core) >= 2 else core
        if tail and tail in jp_text:            # 允许「〜てしまう」讲成「ちゃった」这类活用
            out.append(it)
    return out


def _one_line(zh: str) -> str:
    """译文收成一行（覆盖原文是要直接盖在游戏对话框上的）。

    模型偶尔会「顺手把上文也翻了」，吐出好几行各自独立的译文。这里分两种情况：
    多出来的是很短的语气尾巴（≤12 字，如「真是的。」）→ 合进最长那行；
    多出来的是整句（那是上文被翻了）→ 只保留最长那行。
    """
    if not zh:
        return zh
    lines = [ln.strip() for ln in str(zh).replace("\r", "").split("\n") if ln.strip()]
    if len(lines) <= 1:
        return lines[0] if lines else ""
    main = max(lines, key=len)
    others = [ln for ln in lines if ln is not main]
    if all(len(o) <= 12 for o in others) and len(main) + sum(len(o) for o in others) <= len(main) * 1.5:
        return "".join(lines)          # 原顺序拼回去，保留「……真是的。」这类尾巴
    return main


class Engine:
    def __init__(self, cfg: Config, store: Store, events: "queue.Queue"):
        self.cfg = cfg
        self.store = store
        self.events = events
        self._ocr = None
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._busy = threading.Event()
        self._closed = False  # close() 之后，退出过程中的异常不再当错误上报
        self._last_key = ""
        # (key, 文本, png, 已观察到这颗文本的次数)：稳定性按「文本重复」判定，不看像素是否静止
        self._seen: tuple | None = None
        self._probe_hash = ""                # 上一次的画面指纹
        self._noise_ts = 0.0                 # 上一次提示「像 UI 噪声」的时间
        self._next_delay: float | None = None  # 本轮建议的下次抓取间隔（秒）
        self._seq = 0                        # 第几句（异步语法讲解据此丢弃过期结果）
        self._enrich_busy = False
        self._enrich_next: tuple | None = None
        self._prestart_seq = -1              # 已经提前发过讲解请求的句子号
        self._base_evt: threading.Event | None = None
        self._last_result: dict | None = None  # 最近一次 translate 的完整结果（讲解回填要用）
        self._context: list[str] = []
        self._last_error_ts = 0.0
        self._slow_hint_shown = False
        self._overlay_rect_fn = None      # 悬浮窗屏幕矩形(provider)，截图时排除，见 set_overlay_rect
        self.cover = None                 # ui_cover.CoverWindow：覆盖原文模式
        self.stats = {"ocr": 0, "llm": 0, "cache": 0, "fail": 0, "last_ms": 0}
        self._glyph_px = 0.0                 # 上一帧原文的字高（屏幕像素），覆盖层据此定字号

    # ---------- 翻译引擎选择 ----------
    @property
    def engine_kind(self) -> str:
        """翻译引擎：llm=外部 API（同时讲语法） / ms=微软翻译（只给译文，配本地语法库）。"""
        kind = str(self.cfg.get("translate_engine", "llm") or "llm").strip().lower()
        return "ms" if kind in ("ms", "microsoft", "azure") else "llm"

    @property
    def ms_ready(self) -> bool:
        return bool((self.cfg.get("ms_key") or "").strip())

    def ms_kwargs(self) -> dict:
        return {
            "key": self.cfg.get("ms_key") or "",
            "region": self.cfg.get("ms_region") or "",
            "endpoint": self.cfg.get("ms_endpoint") or msmt.DEFAULT_ENDPOINT,
            "from_lang": self.cfg.get("ms_from_lang") or "ja",
            "to_lang": self.cfg.get("ms_to_lang") or "zh-Hans",
            "timeout": int(self.cfg.get("timeout", 20) or 20),
        }

    def _translate_with_ms(self, jp_text: str, t0: float) -> dict:
        """微软翻译路线：一次请求拿一条译文；语法/生词用本地库补（不花 token）。"""
        zh = msmt.translate(jp_text, **self.ms_kwargs())
        zh = _one_line((zh or "").strip())
        self.stats["llm"] += 1
        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        self.emit("partial", {"jp": jp_text, "zh": zh, "ms": elapsed})
        return {
            "noise": False,
            "jp": jp_text,
            "kana": "",
            "zh": zh,
            "literal": "",
            "notes": "",
            "grammar": grammar_mod.match(jp_text, limit=3),
            "vocab": vocab_mod.quick(jp_text, limit=4),
            "first_ms": elapsed,
            "meta": {"engine": "ms"},
        }

    # ---------- 事件 ----------
    def emit(self, kind: str, data) -> None:
        try:
            self.events.put_nowait((kind, data))
        except queue.Full:
            pass

    def status(self, text: str) -> None:
        self.emit("status", text)

    # ---------- OCR ----------
    def set_overlay_rect(self, fn) -> None:
        """注册「悬浮窗屏幕矩形」的取值函数，截图时把这块从截图里排掉。

        悬浮窗默认就贴在屏幕底部，正好压在 galgame 的对话框上；不排除的话，
        截到的图里会混进上一句的日文和中文译文，每翻一次文本都变，于是永远判不出
        「这一句已经稳定」——表现就是自动翻译彻底不动，只能手按 F9。

        排除方式是「把这块涂黑」：悬浮窗本来就压在这些像素上，用户看不见它们，
        所以涂掉不损失任何能看的信息。另外悬浮窗会自己躲开识别区（见
        Overlay.avoid_rect），正常情况下两者根本不重叠。
        """
        self._overlay_rect_fn = fn

    def _overlay_box(self):
        """悬浮窗屏幕矩形 (x1, y1, x2, y2)，没有则 None。"""
        if self._overlay_rect_fn is None:
            return None
        try:
            r = self._overlay_rect_fn()
        except Exception:                                  # 窗口已销毁等
            return None
        if not r:
            return None
        x1, y1, x2, y2 = (int(v) for v in r)
        if x2 - x1 < 8 or y2 - y1 < 8:
            return None
        return (x1, y1, x2, y2)

    def _capture_region(self) -> tuple[int, int, int, int]:
        """抓取区域（悬浮窗重叠部分由 _exclude_rect 涂黑）。"""
        return tuple(self.cfg.region)

    def _exclude_rect(self, x: int, y: int, w: int, h: int):
        """与抓取区域重叠的悬浮窗部分，涂黑。"""
        box = self._overlay_box()
        if not box:
            return None
        ox1, oy1, ox2, oy2 = box
        if ox2 <= x or ox1 >= x + w or oy2 <= y or oy1 >= y + h:
            return None
        return (ox1, oy1, ox2, oy2)

    def ocr(self):
        if self._ocr is None:
            self._ocr = make_ocr(self.cfg.data)
        return self._ocr

    def preload(self) -> None:
        """启动时预热 OCR（PowerShell + WinRT 初始化约 1~2s），避免第一次抓取卡顿。"""
        try:
            self.ocr().start()
            info = getattr(self._ocr, "info", {}) or {}
            self.status(f"OCR 就绪（winocr lang={info.get('lang', '?')}）")
        except OcrUnavailable as exc:
            self.emit("error", f"OCR 不可用：{exc}")
        except Exception as exc:
            self.emit("error", f"OCR 初始化异常：{exc}")

    def effective_scale(self, w: int, h: int) -> float:
        """把「放大倍数」限制在 OCR 能跑得动的像素预算内。

        框选整块屏幕再乘 2 倍会生成 5000×3000 的图，一帧要 3 秒以上，
        自动翻译就废了；这里按像素预算自动收缩（下限 0.5，不会糊到认不出）。
        """
        want = float(self.cfg.get("upscale", 2.0) or 1.0)
        budget = float(self.cfg.get("ocr_pixel_budget", 3_000_000) or 0)
        area = float(max(1, int(w) * int(h)))
        if budget <= 0:
            return want
        fit = (budget / area) ** 0.5
        return max(0.5, min(want, fit))

    def grab_text(self) -> tuple[str, bytes, float]:
        """截屏 + OCR，返回 (清洗后文本, png, 耗时ms)。"""
        x, y, w, h = self._capture_region()
        if w < 8 or h < 8:
            raise RuntimeError("尚未框选字幕区域")
        t0 = time.perf_counter()
        self._cover_dip(True)
        try:
            png, _, _ = winapi.grab_png(
                x, y, w, h,
                scale=self.effective_scale(w, h),
                binarize=bool(self.cfg.get("binarize", False)),
                exclude=self._exclude_rect(x, y, w, h),
            )
        finally:
            self._cover_dip(False)
        lines = self.ocr().recognize_png(png)
        merged = merge_lines(lines)
        text = strip_nameplate(normalize(display_join(merged)))
        # OCR 会把「上一句的尾巴 / 任务栏 / 聊天窗口」一起读进来，拼成多行乱码。
        # 多行时只留最像台词的那一行（假名最多），整段送去翻译只会得到胡说八道。
        text = pick_dialogue_line(text)
        if not text and bool(self.cfg.get("punct_fallback", True)) and png:
            # Windows OCR 对孤立的「………」一个字符都不给。这时看墨迹形状：
            # 一串大小相近、垂直居中的小点，就按省略号处理，别让画面空着。
            probe = inkprobe.analyze(png)
            if probe.get("ok"):
                text = "……"
                try:
                    ratio = float(probe.get("glyph_ratio") or 0.0)
                    if ratio > 0:
                        self._glyph_px = max(6.0, ratio * h)
                except Exception:
                    pass
        # 原文字号：OCR 给的是放大后的像素，除回去就是屏幕上真实的字高。
        # 覆盖原文要按这个字号画中文，否则一眼就能看出「块不一样大」。
        try:
            scale = self.effective_scale(w, h) or 1.0
            hs = sorted(int(l.get("h") or 0) for l in lines if (l.get("text") or "").strip())
            hs = [v for v in hs if v >= 6]
            got = (hs[len(hs) // 2] / scale) if hs else 0.0
            # 纯标点那句（「………」）的字框只有几个点高，别拿它把字号带跑；
            # 明显比上一句矮一半以上就不采信。
            if got >= 6 and (self._glyph_px <= 0 or got >= 0.55 * self._glyph_px):
                self._glyph_px = got
        except Exception:
            self._glyph_px = 0.0
        self.stats["ocr"] += 1
        ms = (time.perf_counter() - t0) * 1000.0
        if ms > 900 and not self._slow_hint_shown:
            # 大框选是延迟的最大来源：整屏 OCR 是几百毫秒到几秒，框住对话框只要 200ms 左右
            self._slow_hint_shown = True
            self.emit("status", f"这一帧识别花了 {ms:.0f}ms：按 F7 只框住对话框文字区会快很多")
        return text, png, ms

    # ---------- 翻译 ----------
    def translate(self, jp_text: str, png: bytes | None = None) -> dict:
        t0 = time.perf_counter()
        mode = self.cfg.get("mode", "ocr")
        glossary = self.cfg.get("glossary") or ""
        ctx_n = int(self.cfg.get("context_lines", 2) or 0)
        context = self._context[-ctx_n:] if ctx_n else []
        # 上文按「日文 / 已有译文」成对送进对话历史：模型只看得到最后一句要翻，
        # 不会再顺手把上文也翻译成好几行中文（那样一盖上去就是堆叠的乱码）。
        history: list[tuple[str, str]] = []
        for old_jp in context:
            got = self.store.get(old_jp, 0) or {}
            history.append((old_jp, (got.get("zh") or "").strip()))

        if is_punct_only(jp_text):
            # 「………」「!?」：原样搬过去就行，既不该当成噪声丢掉，也没必要花 token。
            out = canon_punct(jp_text)
            res = {"jp": jp_text, "kana": "", "zh": out, "literal": "",
                   "grammar": [], "vocab": [], "notes": "", "punct": True,
                   "meta": {"ms": round((time.perf_counter() - t0) * 1000, 1),
                            "engine": "punct", "cached": False}}
            self._last_result = res
            return res

        if looks_like_ui_noise(jp_text):
            # 「任务栏、调用指令、@文件或对话」「laltl\winapi..py」「用时 11 分 57 秒……」
            # 这些是别的窗口被 OCR 进来了。翻译它们毫无意义，还会被写进缓存反复显示，
            # 所以直接拒掉、不入库；主窗口状态栏顺带提醒用户重新框选。
            skip = {"noise": True, "jp": jp_text, "kana": "", "zh": "",
                    "literal": "", "grammar": [], "vocab": [], "notes": "",
                    "meta": {"ms": round((time.perf_counter() - t0) * 1000, 1),
                             "engine": "skip:ui-noise"}}
            self.emit("status", "框选区里有界面文字（不是游戏台词）：按 F7 只框对话框文字区")
            self._last_result = skip
            return skip

        if self.cfg.get("use_cache", True):
            hit = self.store.get(jp_text, int(self.cfg.get("cache_ttl_days", 0) or 0))
            if hit:
                if is_meta_reply(hit.get("zh") or ""):
                    # 早期版本把模型的推脱话当译文存进了库，命中时直接当脏数据清掉重翻
                    self.store.delete(jp_text)
                    hit = None
                else:
                    hit["zh"] = _one_line(hit.get("zh") or "")
                    self.stats["cache"] += 1
                    hit["meta"] = {"ms": round((time.perf_counter() - t0) * 1000, 1),
                                   "cached": True, "engine": "cache"}
                    self._last_result = hit
                    return hit

        if self.engine_kind == "ms" and not self.ms_ready:
            # 微软翻译模式但没填密钥：本地降级（仍给语法提示），并把该去哪儿填说清楚
            self.emit("status", "微软翻译还没配密钥：设置 → 模型 → 翻译引擎选「微软翻译」并填 KEY 1")
            res = llm.offline_result(jp_text)
            res["meta"] = {"ms": round((time.perf_counter() - t0) * 1000, 1),
                           "engine": "local:no-ms-key"}
            return res

        if self.engine_kind != "ms" and not self.cfg.has_key and (self.cfg.get("provider") != "ollama"):
            # 无 Key：本地降级（仍给语法提示），不报错
            res = llm.offline_result(jp_text)
            res["meta"] = {"ms": round((time.perf_counter() - t0) * 1000, 1), "engine": "local"}
            return res

        kwargs = self.cfg.llm_kwargs()
        try:
            if mode == "vision" and png is not None:
                res = llm.analyze_image(
                    png, kwargs,
                    model=self.cfg.get("vision_model") or None,
                    glossary=glossary, context=context,
                )
                engine = "vision:" + (self.cfg.get("vision_model") or kwargs["model"])
            else:
                if not looks_japanese(jp_text):
                    # 汉化版游戏的台词、系统菜单都是纯中文（没有假名），拿日语模型硬翻就会
                    # 「牛头不对马嘴」。当噪声静默跳过；顺手入库，免得每次轮询都重判。
                    skip = {"noise": True, "jp": jp_text, "kana": "", "zh": "",
                            "literal": "", "grammar": [], "vocab": [], "notes": "",
                            "meta": {"ms": round((time.perf_counter() - t0) * 1000, 1),
                                     "engine": "skip:no-japanese"}}
                    if self.cfg.get("use_cache", True):
                        try:
                            self.store.put(jp_text, skip)
                        except Exception:
                            pass
                    return skip
                res = self._translate_two_phase(jp_text, kwargs, context, glossary, history)
                engine = "ms" if self.engine_kind == "ms" else "text:" + kwargs["model"]
            self.stats["llm"] += 1
        except (llm.LlmError, msmt.MsError) as exc:
            self.stats["fail"] += 1
            raise RuntimeError(f"模型调用失败：{exc}") from None

        if res.get("noise"):
            res["meta"] = {"ms": round((time.perf_counter() - t0) * 1000, 1), "engine": engine}
            return res

        if is_meta_reply(res.get("zh") or ""):
            # 模型把「这看起来不是日文台词…我无法翻译」当译文返回了。绝不能入库
            # （一旦入库，每次轮询都会把这句话当翻译显示）。
            log.warning("模型返回的是推脱/解释，已丢弃：%s", (res.get("zh") or "")[:60])
            self.emit("status", "这一帧没认出日文台词（可能是界面文字或 OCR 噪声）")
            res = {"noise": True, "jp": jp_text, "kana": "", "zh": "",
                   "literal": "", "grammar": [], "vocab": [], "notes": "",
                   "meta": {"ms": round((time.perf_counter() - t0) * 1000, 1),
                            "engine": "reject:meta"}}
            self._last_result = res
            return res

        # 模型没给语法时用本地库兜底；给了就补上等级缺失
        if not res.get("grammar"):
            res["grammar"] = grammar_mod.match(res.get("jp") or jp_text, limit=4)
        prev_meta = res.get("meta") or {}
        res["meta"] = {"ms": round((time.perf_counter() - t0) * 1000, 1), "engine": engine}
        if "seq" in prev_meta:          # 异步讲讲解靠这个号判断是否已翻到下一句
            res["meta"]["seq"] = prev_meta["seq"]
        if prev_meta.get("cut"):        # 断流的半句：标记带过去，下面不缓存
            res["meta"]["cut"] = True
        res["meta"]["first_ms"] = res.get("first_ms")
        res.pop("first_ms", None)
        if (res.get("meta") or {}).get("cut"):
            pass                                        # 半句：只显示、不入库
        elif not (res.get("zh") or "").strip():
            # 译文为空（模型超时、被拒或这行根本不是台词）：不写缓存。
            # 否则下次命中就只剩语法/生词、中文一片空白，还会一直刷新。
            log.info("译文为空，跳过入库：%s", (res.get("jp") or jp_text)[:40])
        elif self.cfg.get("use_cache", True):
            self.store.put(res.get("jp") or jp_text, res)
        self._last_result = res
        return res

    def _translate_two_phase(self, jp_text: str, kwargs: dict,
                            context: list[str], glossary: str,
                            history: list[tuple[str, str]] | None = None) -> dict:
        """先出译文（流式，几百毫秒可见），再补语法讲解。

        旧实现是一次调用同时要译文+语法 JSON：必须等整段 JSON 生成完（2~3 秒）才见到中文。
        拆成两段后，用户先看到译文，语法讲解随后到；第二段失败也不影响译文。
        """
        t0 = time.perf_counter()
        max_tr = int(self.cfg.get("translate_max_tokens", 240) or 240)
        zh = ""
        cut = False
        first_ms: float | None = None

        if self.engine_kind == "ms":
            return self._translate_with_ms(jp_text, t0)

        if bool(self.cfg.get("stream_translate", True)):
            acc: list[str] = []
            last_emit = 0.0
            try:
                for piece in llm.translate_stream(
                    jp_text, kwargs, context=context, glossary=glossary,
                    history=history, max_tokens=max_tr
                ):
                    acc.append(piece)
                    now = time.perf_counter()
                    if first_ms is None:
                        first_ms = (now - t0) * 1000.0
                    if now - last_emit >= 0.07:
                        last_emit = now
                        self.emit("partial", {
                            "jp": jp_text,
                            "zh": "".join(acc).strip(),
                            "ms": round((now - t0) * 1000),
                        })
                zh = "".join(acc).strip()
            except llm.LlmError as exc:
                if not acc:
                    raise
                # 中途断流：已收到的先显示，但绝不当成成品缓存，
                # 否则下次命中缓存会永远显示半句话（覆盖原文时特别扎眼）。
                cut = True
                log.warning("译文流中断，只用已收到部分：%s", exc)
                zh = "".join(acc).strip()
        else:
            zh = llm.translate_once(
                jp_text, kwargs, context=context, glossary=glossary,
                history=history, max_tokens=max_tr
            ).strip()
            first_ms = (time.perf_counter() - t0) * 1000.0

        zh = _one_line(zh)
        if not zh:
            # 流式没吐内容（个别网关不支持 stream）→ 退回原来的一次性解析
            log.warning("流式译文为空，回退到整段解析")
            res = llm.analyze_text(jp_text, kwargs, context=context, glossary=glossary)
            return res

        res = {
            "noise": False,
            "jp": jp_text,
            "kana": "",
            "zh": zh,
            "literal": "",
            "grammar": [],
            "vocab": [],
            "notes": "",
            "first_ms": round(first_ms or 0, 1),
        }
        if cut:
            res["meta"] = {"cut": True}     # 流中断：只显示、不入库

        if bool(self.cfg.get("grammar_phase", True)):
            self.emit("partial", {"jp": jp_text, "zh": zh, "stage": "grammar",
                                  "ms": round((time.perf_counter() - t0) * 1000)})
            if bool(self.cfg.get("grammar_async", True)):
                # 异步补讲解：不让第二轮请求（2~3s）堵住主循环，否则下一句台词要排队等着
                res["grammar"] = grammar_mod.match(jp_text, limit=3)
                # 生词也先给一份离线的（0.04ms），别让「重点词」空着等模型
                res["vocab"] = vocab_mod.quick(jp_text, limit=4)
                res.setdefault("meta", {})["seq"] = self._seq
                if self._prestart_seq == self._seq:
                    self._prestart_seq = -1   # 已经在 _finish_tick 里发过了，别再花一次钱
                else:
                    res["_pending_grammar"] = True
                    self._enqueue_enrich(res, jp_text, zh, kwargs, context, glossary)
            else:
                try:
                    detail = llm.analyze_grammar(
                        jp_text, zh, kwargs, context=context, glossary=glossary,
                        max_tokens=int(self.cfg.get("grammar_max_tokens", 700) or 700),
                    )
                    res["grammar"] = detail.get("grammar") or []
                    res["vocab"] = detail.get("vocab") or []
                    res["kana"] = detail.get("kana") or ""
                    res["literal"] = detail.get("literal") or ""
                    res["notes"] = detail.get("notes") or ""
                except llm.LlmError as exc:
                    # 译文已经拿到了，语法讲解失败只降级，不算整句失败
                    log.warning("语法讲解失败（保留译文）：%s", exc)
                    res["notes"] = "（语法讲解暂时拿不到：" + str(exc)[:80] + "）"
                    res["grammar"] = grammar_mod.match(jp_text, limit=4)
                except Exception as exc:  # 解析异常同理
                    log.warning("语法解析异常（保留译文）：%s", exc)
                    res["grammar"] = grammar_mod.match(jp_text, limit=4)
        else:
            full = llm.analyze_text(jp_text, kwargs, context=context, glossary=glossary)
            res.update({k: full.get(k) for k in ("kana", "literal", "grammar", "vocab", "notes")})
        return res

    # ---------- 异步语法讲解 ----------
    def _prestart_enrich(self, jp_text: str) -> None:
        """翻译还没回来，先把讲解请求发出去（两段请求都只需要日文台词）。

        为什么值得：讲解请求本来要等翻译结束才开始，实测首条语法因此要 2.5 秒；
        并行发出后它的生成时间和翻译重叠，首条语法 1.4 秒就位。界面渲染仍由
        `_base_evt` 卡住——必须等译文那一帧上屏，否则会先弹出「只有语法没译文」的块。
        """
        if not jp_text or not looks_japanese(jp_text):
            log.info("讲解预发起跳过：文本没有日文 %r", (jp_text or "")[:30])
            return
        if self.engine_kind == "ms":
            # 微软翻译模式只做译文，语法/生词取自本地句型库（已在 _translate_with_ms 里补），
            # 不发起大模型请求，也就没有「讲解延迟」这回事。
            return
        if not self.cfg.has_key and (self.cfg.get("provider") != "ollama"):
            log.info("讲解预发起跳过：没有 api_key")
            return
        if self.cfg.get("use_cache", True):
            hit = self.store.get(jp_text, int(self.cfg.get("cache_ttl_days", 0) or 0))
            if hit and (hit.get("grammar") or []):
                return                     # 缓存里已有讲解，别再花钱
        ctx_n = int(self.cfg.get("context_lines", 2) or 0)
        context = self._context[-ctx_n:] if ctx_n else []
        self._base_evt = threading.Event()
        self._prestart_seq = self._seq
        self._enqueue_enrich(
            {"noise": False, "jp": jp_text, "kana": "", "zh": "", "literal": "",
             "grammar": [], "vocab": [], "notes": "", "meta": {"seq": self._seq}},
            jp_text, "", self.cfg.llm_kwargs(), context, self.cfg.get("glossary") or "",
            evt=self._base_evt,
        )

    def _enqueue_enrich(self, base: dict, jp_text: str, zh: str, kwargs: dict,
                        context: list[str], glossary: str,
                        evt: "threading.Event | None" = None) -> None:
        job = (base, jp_text, zh, dict(kwargs), list(context), glossary, evt)
        if self._enrich_busy:
            self._enrich_next = job       # 只保留最新一句，旧的讲解直接丢掉
            return
        self._start_enrich(job)

    def _start_enrich(self, job) -> None:
        self._enrich_busy = True
        threading.Thread(target=self._enrich_run, args=(job,), name="galtl-grammar",
                         daemon=True).start()

    def _enrich_run(self, job) -> None:
        """讲解线程的「外壳」：不管正文怎么退出，都要放开 busy 并启动排队中的下一句。

        WHY：正文里有多条「玩家已经翻页」的提前 return。早先 busy 的复位写在线程末尾，
        提前 return 就把 `_enrich_busy` 永久留在 True——之后所有讲解都塞进 `_enrich_next`
        再也没人启动，界面表现就是「语法永远只有本地那两条，模型讲解再也不出现」。
        """
        try:
            self._run_enrich_job(job)
        except BaseException:                       # 线程里崩掉也要留痕（windowed exe 没 stderr）
            log.exception("讲解线程异常")
        finally:
            self._enrich_busy = False
            nxt = self._enrich_next
            self._enrich_next = None
            if nxt is not None and not self._closed:
                self._start_enrich(nxt)

    def _run_enrich_job(self, job) -> None:
        """第二段：流式补语法/生词，边生成边刷新界面。

        整段 JSON 要等全部生成完（实测 2~3 秒）才解析，所以以前「重点词、语法」总是
        比译文慢好几拍；改成一行一个对象后，第一条语法 0.5~0.9 秒就能上屏，后面陆续补。
        """
        base, jp_text, zh, kwargs, context, glossary, evt = job
        log.info("讲解线程启动 seq=%s prestart=%s zh=%r", (base.get("meta") or {}).get("seq"),
                 evt is not None, (zh or "")[:12])
        if evt is not None:
            evt.wait(6.0)               # 等译文那一帧先上屏（翻译失败时最多等 6 秒）
            log.info("讲解：译文帧已到（或超时），继续")
        detail_ok = False
        seq = (base.get("meta") or {}).get("seq")
        cur = dict(base)
        # 提前发起的讲解拿不到译文；把它从「刚翻完的那句」补齐，否则刷屏时译文会被抹掉
        live = self._last_result
        if live and (live.get("jp") or "") == jp_text:
            for k in ("zh", "kana", "literal"):
                if not cur.get(k) and live.get(k):
                    cur[k] = live[k]
            if not zh:
                zh = str(cur.get("zh") or "")
            cur.setdefault("meta", {})
            if live.get("meta"):
                cur["meta"] = dict(live["meta"])
        cur.pop("_pending_grammar", None)
        # 本地占位可能来自 base（同步路径）或刚翻完的那句 _last_result（并行预发起路径）
        local_g = list(base.get("grammar") or []) or list((live or {}).get("grammar") or [])
        local_v = list(base.get("vocab") or []) or list((live or {}).get("vocab") or [])
        if not local_g:
            local_g = grammar_mod.match(jp_text, limit=3)
        if not local_v:
            local_v = vocab_mod.quick(jp_text, limit=4)
        model_g: list[dict] = []
        model_v: list[dict] = []
        cur["grammar"], cur["vocab"] = local_g, local_v
        emitted = 0
        last_emit = 0.0
        max_tok = int(self.cfg.get("grammar_max_tokens", 520) or 520)

        def merge() -> None:
            """模型结果排在前面，本地中原先没被覆盖的留着——只换内容，不让条数变少。

            早先的做法是「拿到第一条模型语法就清空本地占位」，结果界面上从 2 条语法
            + 2 个生词瞬间掉到 1 条 + 0 个，看起来像退步。
            """
            words = {(x.get("word") or "").strip() for x in model_v}
            kept = _filter_grammar(model_g, jp_text, words)
            if len(kept) != len(model_g):
                log.info("讲解过滤：丢掉原句里对不上的语法点 %d 条", len(model_g) - len(kept))
            gkeys = {_gkey(x.get("pattern", "")) for x in kept}
            vkeys = words
            cur["grammar"] = list(kept) + [
                x for x in local_g if _gkey(x.get("pattern", "")) not in gkeys
            ]
            cur["vocab"] = list(model_v) + [
                x for x in local_v if (x.get("word") or "").strip() not in vkeys
            ]

        def flush(final: bool = False) -> None:
            if self._closed or seq != self._seq:
                log.info("讲解刷新被跳过：closed=%s seq=%s 当前=%s", self._closed, seq, self._seq)
                return
            merge()
            payload = dict(cur)
            payload.pop("_pending_grammar", None)
            payload["enrich"] = True
            payload["streaming"] = not final
            self.emit("result", payload)

        # 流式请求放到自己的线程里：主线程用带超时的 queue.get 等它，这样「上游不回包」
        # 不会把讲解线程永久卡住（打包成 windowed exe 时，卡住的线程连 traceback 都看不到，
        # 界面表现就是「语法一直是本地那两条，模型讲解永远不出现」）。
        items: queue.Queue = queue.Queue()

        def _pump_stream() -> None:
            try:
                for k, it in llm.analyze_grammar_stream(
                    jp_text, zh, kwargs, context=context, glossary=glossary, max_tokens=max_tok
                ):
                    items.put((k, it))
            except BaseException as exc:  # noqa: BLE001  线程里不能把异常吞掉
                items.put(("_err", exc))
            finally:
                items.put(("_done", None))

        threading.Thread(target=_pump_stream, name="galtl-grammar-stream", daemon=True).start()
        log.info("讲解：流式请求已发出 seq=%s", seq)
        first_timeout = float(self.cfg.get("grammar_first_timeout", 12.0) or 12.0)
        total_timeout = float(self.cfg.get("grammar_total_timeout", 40.0) or 40.0)
        first_deadline = time.perf_counter() + first_timeout
        hard_deadline = time.perf_counter() + total_timeout
        stream_error: BaseException | None = None
        timed_out = False
        while True:
            deadline = hard_deadline if detail_ok else first_deadline
            wait = deadline - time.perf_counter()
            if wait <= 0:
                timed_out = True
                log.warning("讲解流式超时（%s），先按已有内容收尾", "首条" if not detail_ok else "全程")
                break
            try:
                kind, item = items.get(timeout=min(wait, 1.0))
            except queue.Empty:
                if self._closed or seq != self._seq:
                    return              # 玩家已经翻到下一句，这份讲解作废
                continue
            if kind == "_done":
                log.info("讲解：流结束 seq=%s 语法%d 生词%d", seq, len(model_g), len(model_v))
                break
            if kind == "_err":
                stream_error = item if isinstance(item, BaseException) else None
                log.warning("讲解：流异常 seq=%s %r", seq, stream_error)
                break
            if self._closed or seq != self._seq:
                return                  # 玩家已经翻到下一句，这份讲解作废
            if kind == "meta":
                for k in ("kana", "literal", "notes"):
                    if item.get(k):
                        cur[k] = item[k]
            elif kind == "g":
                model_g.append(item)
            elif kind == "v":
                model_v.append(item)
            detail_ok = True
            now = time.perf_counter()
            if kind != "meta" and now - last_emit >= 0.06:
                last_emit = now
                emitted += 1
                flush()
        if detail_ok:
            self.stats["llm"] += 1
        if stream_error is not None:
            log.warning("流式讲解失败（译文已显示）：%r", stream_error)
        # 超时就别再花第二次钱；只有「流正常结束却一条都没解析出来」才退回整段请求
        if not detail_ok and not timed_out and stream_error is None:
            try:
                detail = llm.analyze_grammar(
                    jp_text, zh, kwargs, context=context, glossary=glossary,
                    max_tokens=max_tok,
                )
            except Exception as exc2:
                log.warning("语法讲解失败（保留译文，改用本地句型库）：%s", exc2)
            else:
                for k in ("kana", "literal", "notes"):
                    if detail.get(k):
                        cur[k] = detail[k]
                model_g = list(detail.get("grammar") or [])
                model_v = list(detail.get("vocab") or [])
                detail_ok = True
        # 收尾：就算一条语法都没解析出来，也要把「讲解进行中」的标记清掉
        local_g = local_g or grammar_mod.match(jp_text, limit=4)
        local_v = local_v or vocab_mod.quick(jp_text, limit=4)
        merge()
        if self.cfg.get("use_cache", True) and detail_ok:
            try:
                self.store.put(cur.get("jp") or jp_text, cur)
            except Exception:
                pass
        flush(final=True)
        log.info("讲解结束 seq=%s detail_ok=%s 语法%d 生词%d 刷新%d次",
                 seq, detail_ok, len(cur.get("grammar") or []), len(cur.get("vocab") or []), emitted)
        if detail_ok and not self._closed:
            self.status(self._status_line(cur))
        # busy 的复位与「下一句」的启动移到 _enrich_run 的 finally 里——正文可能提前 return

    # ---------- 抓一帧 ----------
    def probe_hash(self) -> str:
        """极低成本的画面指纹（缩小后取哈希），用来判断这一帧要不要 OCR。

        抓整屏 BitBlt + 缩放到 12% 大约 10~20ms，比一次 OCR（200~600ms）便宜一个数量级。
        """
        x, y, w, h = self._capture_region()
        if w < 8 or h < 8:
            raise RuntimeError("尚未框选字幕区域")
        self._cover_dip(True)
        try:
            png, _, _ = winapi.grab_png(x, y, w, h, scale=0.12, binarize=False,
                                        exclude=self._exclude_rect(x, y, w, h))
        finally:
            self._cover_dip(False)
        return hashlib.sha1(png).hexdigest()

    def _cover_dip(self, on: bool) -> None:
        """覆盖层是否需要在取样瞬间隐身（只有系统不支持截图排除时才需要）。"""
        cv = self.cover
        if cv is None or not getattr(cv, "visible", False) or getattr(cv, "excluded", True):
            return
        try:
            cv.dip(on)
        except Exception:
            pass

    def _finish_tick(self, jp_text: str, png: bytes | None, key: str) -> dict:
        self._last_key = key
        self._seen = None
        self._seq += 1
        self.status("翻译中…")
        # 讲解请求只依赖日文台词，和翻译并行发出去，把它的等待藏进翻译的等待里
        if bool(self.cfg.get("grammar_phase", True)) and bool(self.cfg.get("grammar_async", True)):
            self._prestart_enrich(jp_text)
        try:
            result = self.translate(jp_text, png)
        finally:
            evt = self._base_evt
            self._base_evt = None
            if evt is not None:
                evt.set()          # 译文要上屏了，讲解可以开始刷新界面
        result.setdefault("jp_ocr", jp_text)
        if result.get("meta"):
            self.stats["last_ms"] = result["meta"].get("ms", 0)
        if result.get("jp"):
            self._context.append(result["jp"])
            self._context = self._context[-8:]
        return result

    def _confirm(self, seen: tuple, need: int) -> dict | None:
        """又看见同一颗文本一次：次数够、并且「停下够久」才翻译。

        只数次数不够：galgame 的打字机效果每帧多出一两个字，连续两次读到同一句话
        也可能只是恰好卡在同一帧上。真正稳定的台词，像素也要静一会儿。
        """
        key, text, png, hits, t0 = seen
        hits += 1
        settle = float(self.cfg.get("settle_ms", 200) or 0) / 1000.0
        if hits < need or (time.time() - t0) < settle:
            self._seen = (key, text, png, hits, t0)
            self._next_delay = 0.08
            return None
        return self._finish_tick(text, png, key)

    def tick(self, force: bool = False) -> dict | None:
        """抓一次：读到新台词就翻。

        稳定性判据是「同一颗文本连续被读到 stable_reads 次」，**不是**「画面像素静止」——
        galgame 的对话框常年有闪烁的 ▼ 箭头、呼吸立绘、雪花粒子，像素每帧都变，
        用像素判稳定会导致永远确认不了，只能手按 F9（这正是 1.0.6 的病根）。
        """
        mode = self.cfg.get("mode", "ocr")
        need = max(1, int(self.cfg.get("stable_reads", 2)))
        probe_ok = bool(self.cfg.get("use_probe", True)) and mode == "ocr" and not force
        if force:
            self._probe_hash = ""       # 手动触发后重新建立指纹基线
            self._seen = None           # 手动触发不等确认，立刻翻
        self._next_delay = None

        changed = True
        if probe_ok:
            ph = self.probe_hash()
            changed = ph != self._probe_hash
            self._probe_hash = ph
            if not changed:
                seen = self._seen
                if not seen or seen[0] == self._last_key:
                    return None         # 画面没动、也没有待确认的新台词 → 连 OCR 都不做
                # 像素完全一样 ⇒ 文本必然一样：直接给待定台词补一次确认，省掉一次 OCR
                return self._confirm(seen, need)

        jp_text = ""
        png = None
        ocr_ms = 0.0

        if mode == "clipboard":
            jp_text = normalize(_read_clipboard_text())
            if not jp_text:
                return None
        elif mode == "vision":
            x, y, w, h = self.cfg.region
            if w < 8 or h < 8:
                raise RuntimeError("尚未框选字幕区域")
            png, _, _ = winapi.grab_png(
                x, y, w, h, scale=float(self.cfg.get("upscale", 1.5)),
                binarize=False, exclude=self._exclude_rect(),
            )
        else:
            jp_text, png, ocr_ms = self.grab_text()
            self.emit("ocr", {"jp": jp_text, "ms": round(ocr_ms, 1)})

        key = stable_key(jp_text) or dedupe_key(jp_text) if jp_text else (
            f"vision@{hash(png)}" if png else "")
        if not key and jp_text and is_punct_only(jp_text):
            key = "punct:" + canon_punct(jp_text)
        if not key:
            self._seen = None
            return None
        # 同一句话的 OCR 抖动（闪烁 ▼ 被认成假名、零星错字）不该算「新台词」：
        # 否则每帧都加一次 _seq，进行中的流式讲解会被当成「已翻页」丢掉。
        # 台词很长时再放宽一档（0.72）：闪烁箭头常常让 OCR 多认/少认一两个字，
        # 不额外放宽就会出现「明明还是那句話，却一秒重翻一次」的计费。
        same = key == self._last_key or similar_key(key, self._last_key)
        if not same and len(key) >= 12 and len(self._last_key) >= 12:
            same = similar_key(key, self._last_key, threshold=0.72)
        if same and not force:
            self._seen = None
            return None

        if mode == "ocr" and not is_punct_only(jp_text) and not is_meaningful(
            jp_text, min_ratio=float(self.cfg.get("min_ratio", 0.35))
        ):
            # 别静默丢弃：用户看到的只是「自动翻译没反应」，无从判断是框选不对还是参数太严
            now = time.time()
            if now - self._noise_ts > 6:
                self._noise_ts = now
                self.status("这一帧不像台词（日文字符太少），已跳过；按 F7 只框对话框可解决")
            self._seen = None
            return None

        seen = self._seen
        if seen and seen[0] == key:
            if force:
                return self._finish_tick(jp_text, png, key)
            return self._confirm(seen, need)

        # 打字机效果：新一处读到的是「更长的同一句」——这时绝不能翻，等它打完。
        if seen and len(key) > len(seen[0]) and similar_key(key, seen[0], threshold=0.5):
            self._seen = (key, jp_text, png, 1, time.time())
            self._next_delay = 0.10
            return None

        # 第一次看到这颗文本
        if force or need <= 1:
            return self._finish_tick(jp_text, png, key)
        self._seen = (key, jp_text, png, 1, time.time())
        self._next_delay = 0.10       # 很快再看一眼确认，别让用户等一个完整轮询
        return None

    # ---------- 后台循环 ----------
    def start_auto(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._loop, name="galtl-auto", daemon=True)
        self._worker.start()

    def stop_auto(self) -> None:
        self._stop.set()
        self._worker = None

    @property
    def auto_running(self) -> bool:
        return bool(self._worker and self._worker.is_alive() and not self._stop.is_set())

    def _loop(self) -> None:
        self.status("自动翻译运行中")
        while not self._stop.is_set():
            interval = max(0.05, float(self.cfg.get("poll_ms", 250)) / 1000.0)
            t0 = time.time()
            try:
                res = self.tick()
                if res:
                    self.emit("result", res)
                    self.status(self._status_line(res))
            except Exception as exc:
                if self._closed:  # 关闭时的管道断开属正常退出，不刷日志
                    log.debug("退出中，忽略：%s", exc)
                    break
                now = time.time()
                if now - self._last_error_ts > 5:
                    self._last_error_ts = now
                    log.warning("抓取失败：%s", exc)
                    self.emit("error", str(exc))
                self.status(f"等待重试：{exc}")
            delay = self._next_delay if self._next_delay is not None else interval
            rest = delay - (time.time() - t0)
            # 一帧比间隔还慢时不要空转：留 40ms 让出 CPU，否则大框选会把一个核吃满
            self._stop.wait(max(rest, 0.04) if rest > 0 else 0.04)
        self.status("自动翻译已停")

    def _status_line(self, res: dict) -> str:
        meta = res.get("meta") or {}
        tag = "缓存" if meta.get("cached") else (meta.get("engine") or "?")
        s = self.stats
        return (
            f"{tag} {meta.get('ms', 0)}ms ｜ OCR {s['ocr']} · 模型 {s['llm']} · "
            f"缓存命中 {s['cache']} · 失败 {s['fail']}"
        )

    def run_once(self, force: bool = True) -> dict | None:
        self._busy.set()
        try:
            res = self.tick(force=force)
            if res:
                self.emit("result", res)
                self.status(self._status_line(res))
            else:
                self.status("本帧没有新台词")
            return res
        except Exception as exc:
            if self._closed:
                log.debug("退出中，忽略手动抓取异常：%s", exc)
                return None
            log.warning("手动抓取失败：%s", exc)
            self.emit("error", str(exc))
            self.status(f"失败：{exc}")
            return None
        finally:
            self._busy.clear()

    def translate_free(self, text: str) -> dict | None:
        """手动输入日语（或从别处复制）直接解析。"""
        text = normalize(text)
        if not text:
            return None
        try:
            res = self.translate(text, None)
            res.setdefault("jp_ocr", text)
            self.emit("result", res)
            return res
        except Exception as exc:
            self.emit("error", str(exc))
            return None

    def close(self) -> None:
        self._closed = True
        self._enrich_next = None      # 丢掉还没开始的讲解
        self.stop_auto()
        if self._ocr is not None:
            try:
                self._ocr.close()
            except Exception:
                pass
            self._ocr = None


def _read_clipboard_text() -> str:
    """读 Windows 剪贴板文本（galtl 不创建 Tk 时也能用）。

    优先用 Tk（有 Tk 时更稳），失败回落到 ctypes 直读。
    """
    try:
        import tkinter as tk

        r = tk.Tk()
        r.withdraw()
        try:
            return r.clipboard_get()
        finally:
            r.destroy()
    except Exception:
        pass
    try:
        import ctypes
        import ctypes.wintypes as wt

        u32 = ctypes.WinDLL("user32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        u32.OpenClipboard.argtypes = [ctypes.c_void_p]
        u32.GetClipboardData.restype = ctypes.c_void_p
        u32.GetClipboardData.argtypes = [ctypes.c_uint]
        u32.CloseClipboard.argtypes = []
        if not u32.OpenClipboard(None):
            return ""
        try:
            h = u32.GetClipboardData(13)  # CF_UNICODETEXT
            if not h:
                return ""
            ptr = k32.GlobalLock(ctypes.c_void_p(h))
            if not ptr:
                return ""
            try:
                return ctypes.wstring_at(ptr)
            finally:
                k32.GlobalUnlock(ctypes.c_void_p(h))
        finally:
            u32.CloseClipboard()
    except Exception:
        return ""
