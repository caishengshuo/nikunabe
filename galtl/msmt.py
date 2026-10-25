"""微软翻译（Azure Translator Text v3）客户端。

为什么单独成模块：它跟 `llm.py` 是两条完全不同的路——
  * 大模型：OpenAI 兼容 /chat/completions，能同时给译文+语法+生词，按 token 计费；
  * 微软翻译：`POST {endpoint}/translate?api-version=3.0`，只给译文，按字符计费，
    免费额度 200 万字符/月，日语→中文的小句子足够用很久。

配置（设置 → 模型 → 翻译引擎 = 微软翻译）：
  ms_key       Azure 资源的密钥（密钥和终结点页里的 KEY 1）
  ms_region    资源区域，如 eastasia / japaneast（全球资源可留空）
  ms_endpoint  默认 https://api.cognitive.microsofttranslator.com

只依赖标准库，跟其它模块一致。
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
import uuid
from typing import Sequence

log = logging.getLogger("galtl.msmt")

DEFAULT_ENDPOINT = "https://api.cognitive.microsofttranslator.com"
API_VERSION = "3.0"


class MsError(RuntimeError):
    """微软翻译调用失败；消息直接给用户看（已写成中文、带处理办法）。"""


def _endpoint(endpoint: str) -> str:
    ep = (endpoint or DEFAULT_ENDPOINT).strip().rstrip("/")
    if not ep:
        ep = DEFAULT_ENDPOINT
    if not ep.startswith("http"):
        ep = "https://" + ep
    return ep


def translate_batch(
    texts: Sequence[str],
    *,
    key: str,
    region: str = "",
    endpoint: str = DEFAULT_ENDPOINT,
    from_lang: str = "ja",
    to_lang: str = "zh-Hans",
    timeout: int = 20,
) -> list[str]:
    """批量翻译；返回与输入等长的列表。空串原样返回，不发请求。"""
    items = list(texts)
    if not items:
        return []
    if not (key or "").strip():
        raise MsError(
            "还没填微软翻译的密钥：设置 → 模型 → 翻译引擎选「微软翻译」，"
            "把 Azure 门户里「密钥和终结点」的 KEY 1 粘进去。"
        )

    payload = [{"Text": t} for t in items]
    url = (
        f"{_endpoint(endpoint)}/translate"
        f"?api-version={API_VERSION}&from={from_lang}&to={to_lang}"
    )
    headers = {
        "Content-Type": "application/json",
        "Ocp-Apim-Subscription-Key": key.strip(),
        "X-ClientTraceId": str(uuid.uuid4()),
        "User-Agent": "galgame-jp-helper",
    }
    if (region or "").strip():
        headers["Ocp-Apim-Subscription-Region"] = region.strip()

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise MsError(_explain(exc.code, detail)) from exc
    except urllib.error.URLError as exc:
        raise MsError(f"连不上微软翻译（{exc.reason}）：检查网络或代理，也可以把 ms_endpoint 换成你能访问的镜像。") from exc
    except TimeoutError as exc:
        raise MsError(f"微软翻译超时（>{timeout}s）：网络慢或字数太多，稍后重试。") from exc

    try:
        data = json.loads(raw)
    except Exception as exc:
        raise MsError(f"微软翻译返回的不是 JSON：{raw[:200]}") from exc

    out: list[str] = []
    for item in data if isinstance(data, list) else []:
        tr = (item or {}).get("translations") or []
        out.append(str(tr[0].get("text") if tr else "") or "")
    if len(out) != len(items):
        raise MsError(
            f"微软翻译返回条数不对（发 {len(items)} 收 {len(out)}）：可能是字符数超限或接口改版。"
        )
    log.info("微软翻译 %d 句 %.0fms", len(items), (time.perf_counter() - t0) * 1000)
    return out


def translate(text: str, **kw) -> str:
    """单句翻译的便捷入口。"""
    t = (text or "").strip()
    if not t:
        return ""
    return translate_batch([t], **kw)[0]


def _explain(code: int, detail: str) -> str:
    """把 Azure 的错误码翻成能直接照做的中文。"""
    tail = f"（{detail}）" if detail else ""
    if code == 401:
        return ("微软翻译鉴权失败（401）：密钥不对或已失效。到 Azure 门户 → 翻译器资源 → "
                "密钥和终结点，重新复制 KEY 1。" + tail)
    if code == 403:
        return ("微软翻译拒绝访问（403）：多数是区域填错——把 ms_region 改成资源所在的区域"
                "（如 eastasia / japaneast），全球资源则留空。" + tail)
    if code == 429:
        return ("微软翻译限流了（429）：免费额度用完或请求太密。等一会儿，"
                "或把「自动翻译间隔」调大。" + tail)
    if code == 400:
        return ("微软翻译说请求不合法（400）：检查 from/to 语言代码（日语 ja、简体中文 zh-Hans）。" + tail)
    return f"微软翻译调用失败（HTTP {code}）{tail}"
