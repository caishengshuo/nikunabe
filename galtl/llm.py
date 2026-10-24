"""OpenAI 兼容 /chat/completions 客户端 + 翻译/语法解析提示词。

只用标准库 urllib，支持 DeepSeek / 通义 / 豆包 / MiniMax / OpenAI / Ollama / 任意兼容网关。
"""
from __future__ import annotations

import base64
import json
import logging
import re
import urllib.error
import urllib.request

from . import grammar as grammar_mod

log = logging.getLogger("galtl.llm")

# 常用渠道预设：base_url + 默认模型，设置页一键切换
PROVIDERS: dict[str, dict] = {
    "deepseek": {
        "label": "DeepSeek（纯文本，便宜）",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "vision_model": "",
        "need_key": True,
        "hint": "https://platform.deepseek.com/api_keys",
    },
    "qwen": {
        "label": "通义千问 DashScope（有视觉模型）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "vision_model": "qwen-vl-max",
        "need_key": True,
        "hint": "https://bailian.console.aliyun.com/",
    },
    "doubao": {
        "label": "豆包 / 火山方舟（有视觉模型）",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-pro-32k",
        "vision_model": "doubao-vision-pro-32k",
        "need_key": True,
        "hint": "https://console.volcengine.com/ark",
    },
    "minimax": {
        "label": "MiniMax",
        "base_url": "https://api.minimax.chat/v1",
        "model": "abab6.5s-chat",
        "vision_model": "",
        "need_key": True,
        "hint": "https://platform.minimaxi.com/",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "vision_model": "gpt-4o-mini",
        "need_key": True,
        "hint": "https://platform.openai.com/api-keys",
    },
    "ollama": {
        "label": "Ollama 本地（免费，需先 ollama serve）",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b",
        "vision_model": "qwen2.5vl:7b",
        "need_key": False,
        "hint": "ollama pull qwen2.5:7b",
    },
    "lmstudio": {
        "label": "LM Studio 本地",
        "base_url": "http://127.0.0.1:1234/v1",
        "model": "local-model",
        "vision_model": "",
        "need_key": False,
        "hint": "启动 LM Studio 并开启 Local Server",
    },
    "custom": {
        "label": "自定义 OpenAI 兼容端点",
        "base_url": "",
        "model": "",
        "vision_model": "",
        "need_key": True,
        "hint": "填到 /v1 为止",
    },
}

SYSTEM_PROMPT = """你是资深日语教师 + 视觉小说（galgame）译者，读者是中文母语的日语学习者。
任务：把给定的日文台词翻译成中文，并讲清其中对理解起决定作用的语法。

硬性要求：
1. 只输出一个 JSON 对象，不要 markdown 代码块，不要任何解释性前言。
2. 日文台词常来自 OCR，可能有错字（如 だ/た、は/ほ、浊点丢失）。先据语境纠正，原文放回 jp 字段。
3. 翻译用自然口语化的简体中文，贴合角色说话方式（傲娇、敬语、古风、方言要在 notes 里点出），不要文言腔，不要逐字硬译。
4. 语法点只挑 2-4 条真正影响理解的：优先本句实际出现的句式、助词、时体、授受、敬语、使役/受身、条件形。不要塞无关条目凑数。
5. 每条语法必须给「本句中的用法」：指出从原句摘出的片段（form），并说明这里为什么这样变形/这样用。
6. 若识别文本是无意义噪声（UI 按钮、乱码、不足 2 个日文字符），返回 {"noise": true}。

JSON 结构：
{
  "noise": false,
  "jp": "纠正 OCR 后的日文原文（保持原样一段）",
  "kana": "整句平假名读音（汉字注音，片假名保留；无把握可留空）",
  "zh": "中文翻译",
  "literal": "贴近字面的直译（简短一句，可为空）",
  "grammar": [
    {"pattern": "〜てしまう", "level": "N4", "meaning": "…完了／遗憾地…了",
     "explain": "在【本句】中的作用说明，1-2 句", "form": "摘出的贴合片段"}
  ],
  "vocab": [
    {"word": "词", "reading": "读音", "pos": "词性", "meaning": "中文义"}
  ],
  "notes": "语气、双关、省略的主体、敬语对象、语境暗示（可为空字符串）"
}"""

VISION_ADDON = """
本次输入是游戏画面截图而不是文字。请先准确读出文本框里的日文（忽略窗口边框、按钮、菜单、状态栏、名字标注框，只取角色台词），再按上面的 JSON 结构输出。
读不出来的地方按语境补全，不要编造与画面无关的内容。"""


class LlmError(RuntimeError):
    pass


def _post(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:500]
        except Exception:
            pass
        raise LlmError(f"HTTP {exc.code}：{detail or exc.reason}") from None
    except urllib.error.URLError as exc:
        raise LlmError(f"连接失败：{exc.reason}（检查 base_url / 网络 / 本地服务是否启动）") from None
    except TimeoutError:
        raise LlmError("请求超时，可在设置里加大 timeout") from None
    try:
        return json.loads(body)
    except Exception:
        raise LlmError(f"返回不是 JSON：{body[:300]}") from None


_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal")


def _needs_key(base_url: str) -> bool:
    """本地推理服务（Ollama / LM Studio / vLLM 本机）允许不填 key。"""
    low = (base_url or "").lower()
    return not any(h in low for h in _LOCAL_HOSTS)


def chat(
    messages: list[dict],
    *,
    base_url: str,
    model: str,
    api_key: str = "",
    temperature: float = 0.3,
    max_tokens: int = 1200,
    timeout: int = 60,
    extra_headers: dict | None = None,
    json_mode: bool = True,
) -> str:
    if not base_url:
        raise LlmError("未配置 base_url：设置 → 模型里填接口地址（DeepSeek 是 https://api.deepseek.com/v1）")
    if not model:
        raise LlmError("未配置 model：设置 → 模型里选或填模型名")
    if not api_key and _needs_key(base_url):
        raise LlmError(
            "还没填 API Key：设置 → 模型粘贴 key（本地 Ollama / LM Studio 可以留空）。"
            "没有 key 也能用：主窗口右侧的语法提示与 118 条句型库是离线的。"
        )
    url = base_url.rstrip("/") + "/chat/completions"
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    headers.update(extra_headers or {})
    body = _post(url, payload, headers, timeout)
    if "error" in body and not body.get("choices"):
        raise LlmError(str(body["error"])[:300])
    try:
        msg = body["choices"][0]["message"]
    except Exception:
        raise LlmError(f"响应结构异常：{json.dumps(body, ensure_ascii=False)[:300]}") from None
    content = msg.get("content")
    if isinstance(content, list):  # 少数网关返回分段内容
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return (content or "").strip()


_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _balance_json(s: str) -> str:
    """把被 max_tokens 截断的 JSON 补成合法结构：补齐未闭合的字符串和括号。"""
    out: list[str] = []
    in_str = False
    esc = False
    stack: list[str] = []
    for ch in s:
        out.append(ch)
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                stack.append("}")
            elif ch == "[":
                stack.append("]")
            elif ch in "}]" and stack:
                stack.pop()
    res = "".join(out)
    if esc:
        res = res[:-1]
    if in_str:
        res += '"'
    res = re.sub(r",\s*$", "", res.rstrip())
    return res + "".join(reversed(stack))


def _salvage_fields(s: str) -> dict:
    """结构实在修不好时，把 jp/zh/literal/notes 这几个字符串字段硬抠出来。"""
    got: dict[str, str] = {}
    for key in ("jp", "zh", "literal", "notes"):
        m = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"', s)
        if m:
            try:
                got[key] = json.loads(f'"{m.group(1)}"')
            except Exception:
                got[key] = m.group(1)
    return got


def parse_json(text: str) -> dict:
    """从模型输出里抠出 JSON：容忍代码块、前后废话、尾逗号、中文引号、截断。"""
    if not text:
        raise LlmError("模型返回为空")
    s = _JSON_FENCE.sub("", text).strip()
    start = s.find("{")
    if start < 0:
        raise LlmError(f"未找到 JSON：{text[:200]}")
    s = s[start:]
    end = s.rfind("}")
    if end >= 0 and not s[end + 1 :].strip():
        s = s[: end + 1]        # 尾部干净：按常规截取
    # 否则可能是被 max_tokens 截断或尾随垃圾，留给下面的修复逻辑
    try:
        return json.loads(s)
    except Exception:
        pass
    repaired = re.sub(r",\s*([}\]])", r"\1", s)          # 尾逗号
    repaired = repaired.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    repaired = re.sub(r"(?<!\\)\n", " ", repaired)
    for cand in (repaired, _balance_json(repaired), _balance_json(s)):
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    salvaged = _salvage_fields(s)
    if salvaged.get("zh") or salvaged.get("jp"):
        log.warning("JSON 结构损坏，已按字段抢救：%s", list(salvaged))
        return salvaged
    raise LlmError(f"JSON 解析失败：{s[:200]}")


def _ensure_schema(obj: dict, fallback_jp: str = "") -> dict:
    out = {
        "noise": bool(obj.get("noise")),
        "jp": (obj.get("jp") or fallback_jp or "").strip(),
        "kana": (obj.get("kana") or "").strip(),
        "zh": (obj.get("zh") or "").strip(),
        "literal": (obj.get("literal") or "").strip(),
        "grammar": [],
        "vocab": [],
        "notes": (obj.get("notes") or "").strip(),
    }
    for g in obj.get("grammar") or []:
        if not isinstance(g, dict):
            continue
        pat = (g.get("pattern") or "").strip()
        if not pat:
            continue
        out["grammar"].append(
            {
                "pattern": pat,
                "level": (g.get("level") or "").strip(),
                "meaning": (g.get("meaning") or "").strip(),
                "explain": (g.get("explain") or "").strip(),
                "form": (g.get("form") or "").strip(),
                "source": "llm",
            }
        )
    for v in obj.get("vocab") or []:
        if not isinstance(v, dict):
            continue
        w = (v.get("word") or "").strip()
        if not w:
            continue
        out["vocab"].append(
            {
                "word": w,
                "reading": (v.get("reading") or "").strip(),
                "pos": (v.get("pos") or "").strip(),
                "meaning": (v.get("meaning") or "").strip(),
            }
        )
    return out


def build_messages(
    jp_text: str,
    *,
    context: list[str] | None = None,
    glossary: str = "",
) -> list[dict]:
    hints = grammar_mod.as_hint(jp_text)
    parts = []
    if glossary.strip():
        parts.append("【术语表，翻译时严格沿用】\n" + glossary.strip())
    if context:
        ctx = "\n".join(f"（前文{i+1}）{c}" for i, c in enumerate(reversed(context)) if c)
        if ctx:
            parts.append("【上文，仅供判断指代与语气，不要翻译】\n" + ctx)
    parts.append("【本地句型库命中，供参考；若不适用请忽略，不要硬套】\n" + hints)
    parts.append("【待处理的日文】\n" + jp_text.strip())
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def analyze_text(jp_text: str, kwargs: dict, *, context=None, glossary: str = "") -> dict:
    msgs = build_messages(jp_text, context=context, glossary=glossary)
    raw = chat(msgs, **kwargs)
    return _ensure_schema(parse_json(raw), fallback_jp=jp_text)


def analyze_image(png: bytes, kwargs: dict, *, model: str | None = None,
                  glossary: str = "", context=None) -> dict:
    """直接把截图交给多模态模型，省掉本地 OCR（花字/竖排更稳）。"""
    b64 = base64.b64encode(png).decode("ascii")
    user = [{"type": "text", "content": "请处理这张 galgame 画面截图。"}]
    if glossary.strip():
        user.append({"type": "text", "content": "【术语表】\n" + glossary.strip()})
    if context:
        user.append({"type": "text", "content": "【上文】\n" + "\n".join(context)})
    user.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT + VISION_ADDON},
        {"role": "user", "content": user},
    ]
    kw = dict(kwargs)
    if model:
        kw["model"] = model
    raw = chat(msgs, **kw)
    obj = _ensure_schema(parse_json(raw))
    if not obj["jp"]:
        obj["jp"] = "（视觉模型未读出文本）"
    return obj


def offline_result(jp_text: str) -> dict:
    """无 API Key 时的降级结果：只给本地句型库的语法提示。"""
    hits = grammar_mod.match(jp_text, limit=6)
    return {
        "noise": False,
        "jp": jp_text,
        "kana": "",
        "zh": "（未配置模型 API Key，仅显示本地语法提示）",
        "literal": "",
        "grammar": hits,
        "vocab": [],
        "notes": "在「设置 → 模型」填入 base_url / model / api_key 后即可自动翻译。",
        "offline": True,
    }


# ============================================================================
# 低延迟两段式：先流式出译文（几百毫秒就能看到中文），再补语法讲解
# ============================================================================

TRANSLATE_SYSTEM = """你是 galgame 译者。把日文台词译成自然口语化的简体中文。
只翻译【日文】标记的那一句；【上文】和【术语表】只用来判断指代、性别与语气，绝对不要翻译、不要出现在输出里。
只输出这一句的译文：单行中文，不要换行、不要引号、不要「译文：」前缀、不要解释、不要保留日文。
三条硬规矩（违反就是翻错）：
1) 译文必须和【日文】说的是同一件事。日文只喊了个称呼、只有半句、只有语气词，就照实译成相应的招呼/语气，**不要补全、不要编出原文没有的内容**。
2) 原文没说完也不要自己圆：宁可用「……」收尾，也不要加戏。
3) 口语语气词要译出语气，不译字面：ってば／ったら = 「我说…啊」「你倒是…」；って = 「说是…」；ぞ／ぜ = 「…哦／…啊」；かよ = 「…吗！」。"""

GRAMMAR_SYSTEM = """你是日语教师，读者是中文母语的日语学习者。针对给定日文台词（和已有译文）输出讲解。
只输出一个 JSON 对象，不要 markdown 代码块，不要任何前言。结构：
{"kana":"整句平假名读音（汉字注音，片假名保留；可空）","literal":"贴合字面的直译（可空）",
 "grammar":[{"pattern":"〜てしまう","level":"N4","meaning":"…完了／遗憾地…了","explain":"在本句中的作用，1-2 句","form":"从原句摘出的片段"}],
 "vocab":[{"word":"词","reading":"读音","pos":"词性","meaning":"中文义"}],
 "notes":"语气、双关、省略的主语、敬语对象（可空）"}
要求：grammar 2-4 条，只挑真正影响理解的，每条必须给 explain 与 form；vocab 3-5 个；不要重复翻译。"""


def _endpoint(base_url: str, model: str, api_key: str) -> tuple[str, dict]:
    """校验配置并返回 (url, headers)；报错信息都带上操作路径。"""
    if not base_url:
        raise LlmError("未配置 base_url：设置 → 模型里填接口地址（DeepSeek 是 https://api.deepseek.com/v1）")
    if not model:
        raise LlmError("未配置 model：设置 → 模型里选或填模型名")
    if not api_key and _needs_key(base_url):
        raise LlmError(
            "还没填 API Key：设置 → 模型粘贴 key（本地 Ollama / LM Studio 可以留空）。"
            "没有 key 也能用：主窗口右侧的语法提示与 118 条句型库是离线的。"
        )
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    return base_url.rstrip("/") + "/chat/completions", headers


def _post_stream(url: str, payload: dict, headers: dict, timeout: int):
    """POST /chat/completions 并按 SSE 逐块吐出文本增量。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "text/event-stream")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:500]
        except Exception:
            pass
        raise LlmError(f"HTTP {exc.code}：{detail or exc.reason}") from None
    except urllib.error.URLError as exc:
        raise LlmError(f"连接失败：{exc.reason}（检查 base_url / 网络 / 本地服务是否启动）") from None
    except TimeoutError:
        raise LlmError("请求超时，可在设置里加大 timeout") from None
    with resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line or not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if chunk == "[DONE]":
                break
            try:
                obj = json.loads(chunk)
            except Exception:
                continue
            if obj.get("error") and not obj.get("choices"):
                raise LlmError(str(obj["error"])[:300])
            try:
                choice = obj["choices"][0]
            except Exception:
                continue
            piece = (choice.get("delta") or {}).get("content")
            if piece is None:
                piece = (choice.get("message") or {}).get("content")
            if piece:
                yield piece


def chat_stream(
    messages: list[dict],
    *,
    base_url: str,
    model: str,
    api_key: str = "",
    temperature: float = 0.3,
    max_tokens: int = 240,
    timeout: int = 60,
    extra_headers: dict | None = None,
):
    """流式 chat：逐块 yield 文本增量（不做 json_mode，用于「先出译文」）。"""
    url, headers = _endpoint(base_url, model, api_key)
    headers.update(extra_headers or {})
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    yield from _post_stream(url, payload, headers, timeout)


def _slim_parts(jp_text: str, context: list[str] | None, glossary: str) -> list[str]:
    parts = []
    if glossary.strip():
        parts.append("【术语表，必须沿用】\n" + glossary.strip())
    if context:
        ctx = "\n".join(f"（前文）{c}" for c in context[-2:] if c)
        if ctx:
            parts.append("【上文，仅判断指代与语气】\n" + ctx)
    parts.append("【日文】\n" + jp_text.strip())
    return parts


def _history_messages(history, jp_text: str) -> list[dict]:
    """把上文拼成真正的对话历史。

    以前上文和待翻句一起塞进同一条 user 消息，模型时不时「顺手把上文也翻了」，
    输出好几行中文；覆盖原文时那几行会一起盖到游戏框里，非常难看。
    改成「历史轮次」之后，待翻的只有最后一句。
    """
    msgs: list[dict] = []
    for jp, zh in (history or [])[-4:]:
        if not jp:
            continue
        msgs.append({"role": "user", "content": "【日文】\n" + str(jp).strip()})
        if zh:
            msgs.append({"role": "assistant", "content": str(zh).strip()})
    msgs.append({"role": "user", "content": "【日文】\n" + jp_text.strip()})
    return msgs


def translate_stream(
    jp_text: str,
    kwargs: dict,
    *,
    context: list[str] | None = None,
    glossary: str = "",
    history: list[tuple[str, str]] | None = None,
    max_tokens: int = 240,
):
    """第一段：只要译文。逐块 yield 中文增量，调用方可以边收边显示。"""
    head = [{"role": "system", "content": TRANSLATE_SYSTEM}]
    if glossary.strip():
        head[0] = {"role": "system",
                   "content": TRANSLATE_SYSTEM + "\n\n【术语表，必须沿用】\n" + glossary.strip()}
    if history:
        msgs = head + _history_messages(history, jp_text)
    else:
        msgs = head + [{"role": "user",
                        "content": "\n\n".join(_slim_parts(jp_text, context, glossary))}]
    kw = dict(kwargs)
    kw.pop("max_tokens", None)
    kw["max_tokens"] = max_tokens
    kw.pop("json_mode", None)
    yield from chat_stream(msgs, **kw)


def translate_once(
    jp_text: str,
    kwargs: dict,
    *,
    context: list[str] | None = None,
    glossary: str = "",
    history: list[tuple[str, str]] | None = None,
    max_tokens: int = 240,
) -> str:
    """第一段的非流式版本（网关不支持 stream 时用）。"""
    head = [{"role": "system", "content": TRANSLATE_SYSTEM}]
    if glossary.strip():
        head[0] = {"role": "system",
                   "content": TRANSLATE_SYSTEM + "\n\n【术语表，必须沿用】\n" + glossary.strip()}
    if history:
        msgs = head + _history_messages(history, jp_text)
    else:
        msgs = head + [{"role": "user",
                        "content": "\n\n".join(_slim_parts(jp_text, context, glossary))}]
    kw = dict(kwargs)
    kw["max_tokens"] = max_tokens
    kw["json_mode"] = False
    return chat(msgs, **kw)


def analyze_grammar(
    jp_text: str,
    zh_text: str,
    kwargs: dict,
    *,
    context: list[str] | None = None,
    glossary: str = "",
    max_tokens: int = 700,
) -> dict:
    """第二段：语法/生词/语气。先给本地句型库命中当提示，模型只做取舍与讲解。"""
    hints = grammar_mod.as_hint(jp_text)
    parts = _slim_parts(jp_text, context, glossary)
    if hints:
        parts.insert(0, "【本地句型库命中，供参考；不适用就忽略】\n" + hints)
    if zh_text:
        parts.insert(0, "【已有译文，不要重复翻译】\n" + zh_text)
    msgs = [
        {"role": "system", "content": GRAMMAR_SYSTEM},
        {"role": "user", "content": "\n\n".join(parts)},
    ]
    kw = dict(kwargs)
    kw["max_tokens"] = max_tokens
    kw["json_mode"] = True
    return _ensure_schema(parse_json(chat(msgs, **kw)), fallback_jp=jp_text)


# 流式版：一行一个 JSON 对象，做完一条就发一条，界面能一条条往上长
GRAMMAR_JSONL_SYSTEM = """你是日语教师，读者是中文母语的日语学习者。针对给定日文台词输出讲解。
**只输出若干行 JSON，每行一个完整对象，行间只用换行分隔**；不要数组、不要 markdown、不要前言、不要解释。
输出顺序固定（重点先出，读者不用等最后一行）：
1) 先每行一个语法点（2-4 行），必须给 explain 与 form：
{"t":"g","pattern":"〜てしまう","level":"N4","meaning":"…完了／遗憾地…了","explain":"在本句中的作用，1-2 句","form":"从原句摘出的片段"}
2) 再每行一个生词（3-5 行）：
{"t":"v","word":"词","reading":"读音","pos":"词性","meaning":"中文义"}
3) 最后一行才是读音与语气：
{"t":"meta","kana":"整句平假名读音","literal":"贴合字面的直译","notes":"语气/双关/省略的主语/敬语对象"}
三条硬规矩：
A) pattern 必须是原句里**真实出现**的句型；form 必须是从原句原样摘出的片段（能和原句对上）。对不上就别列。
B) 单词不是语法：名词/称呼（如「お客さん」）只进 vocab，不要塞进 grammar。
C) 口语缩略别拆错：「〜ってば／〜ったら」是催促对方听的终助词（「我说…啊」「喂，你倒是…」），**不是**「〜ては」也不是「〜ば（假定形）」；「〜ちゃう／〜じゃう」是「〜てしまう」的口语。
只挑真正影响理解的语法点；不要重复翻译整句。"""


def _jsonl_items(text: str):
    """从累计文本里切出所有完整的 JSON 行，返回 (items, rest)。

    注意：**不能对整段做 strip()**。流式时最后一行通常还没收到换行符，strip() 会把
    刚到的 "\\n" 也吃掉，于是永远等不到行结束、缓冲区无限增长（实测就是这样一条都
    解析不出来）。这里只清掉开头的空行，尾部原样留着。
    """
    items: list[dict] = []
    buf = text.lstrip("\r\n \t")
    while True:
        nl = buf.find("\n")
        if nl < 0:
            break                       # 还没有完整的一行，等下一个 chunk
        line = buf[:nl].strip()
        rest = buf[nl + 1:]
        if not line or line.startswith("```") or line in ("{", "}", "[", "]"):
            buf = rest
            continue
        obj = _try_object(line)
        if obj is None:
            if line.endswith("}") or line.endswith("]"):
                # 有结尾却解析不动：丢掉这一行，继续往下，别让坏行堵死后面所有内容
                buf = rest
                continue
            break                       # 可能是被换行拆开的对象，等更多内容再拼
        items.append(obj)
        buf = rest
    return items, buf


def _try_object(line: str):
    s = line.strip().rstrip(",")
    if not s.startswith("{"):
        return None
    try:
        obj = json.loads(s)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def analyze_grammar_stream(
    jp_text: str,
    zh_text: str,
    kwargs: dict,
    *,
    context: list[str] | None = None,
    glossary: str = "",
    max_tokens: int = 520,
):
    """流式第二段：边生成边 yield ("meta"|"g"|"v", dict)。

    为什么这么写：整段 JSON 必须等模型写完最后一个字（实测 2~3 秒）才解析；改成一行
    一个对象后，第一个语法点生成完（0.5~0.9 秒）就能显示，剩下的陆续补上。
    """
    hints = grammar_mod.as_hint(jp_text)
    parts = _slim_parts(jp_text, context, glossary)
    if hints:
        parts.insert(0, "【本地句型库命中，供参考；不适用就忽略】\n" + hints)
    if zh_text:
        parts.insert(0, "【已有译文，不要重复翻译】\n" + zh_text)
    msgs = [
        {"role": "system", "content": GRAMMAR_JSONL_SYSTEM},
        {"role": "user", "content": "\n\n".join(parts)},
    ]
    kw = dict(kwargs)
    kw.pop("json_mode", None)
    kw.pop("max_tokens", None)      # 本函数自己给 max_tokens，重复传会 TypeError
    buf = ""
    seen: set[str] = set()
    for piece in chat_stream(msgs, max_tokens=max_tokens, **kw):
        buf += piece
        items, buf = _jsonl_items(buf)
        for obj in items:
            kind = str(obj.get("t") or "").strip().lower()
            if kind == "meta":
                yield ("meta", {k: obj.get(k, "") for k in ("kana", "literal", "notes")})
            elif kind in ("g", "grammar"):
                p = str(obj.get("pattern") or "").strip()
                if not p or p in seen:
                    continue
                seen.add(p)
                yield ("g", {k: obj.get(k, "") for k in
                             ("pattern", "level", "meaning", "explain", "form")})
            elif kind in ("v", "vocab", "w"):
                w = str(obj.get("word") or "").strip()
                if not w or w in seen:
                    continue
                seen.add(w)
                yield ("v", {k: obj.get(k, "") for k in ("word", "reading", "pos", "meaning")})
    # 最后一行可能没有换行结尾
    items, _rest = _jsonl_items(buf + "\n")
    for obj in items:
        kind = str(obj.get("t") or "").strip().lower()
        if kind in ("g", "grammar"):
            p = str(obj.get("pattern") or "").strip()
            if p and p not in seen:
                seen.add(p)
                yield ("g", {k: obj.get(k, "") for k in
                             ("pattern", "level", "meaning", "explain", "form")})
        elif kind in ("v", "vocab", "w"):
            w = str(obj.get("word") or "").strip()
            if w and w not in seen:
                seen.add(w)
                yield ("v", {k: obj.get(k, "") for k in ("word", "reading", "pos", "meaning")})
        elif kind == "meta":
            yield ("meta", {k: obj.get(k, "") for k in ("kana", "literal", "notes")})
