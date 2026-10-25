"""翻译引擎切换回归：微软翻译（Azure v3）与外部 API 两条路都要能独立工作。

跑法：`python engine_switch_check.py`，全过 exit=0。

覆盖的点：
  1) 配置默认值与 engine_kind 判定（llm / ms / 别名）
  2) ms 模式没填 key → 本地降级，不抛异常、不发请求
  3) ms 模式带 key → 真的发 HTTP（打到本地假 Azure），译文/语法/生词齐活，
     且请求带对了 path、Ocp-Apim-Subscription-Key、Ocp-Apim-Subscription-Region
  4) 401 / 429 的错误消息是人话（带怎么办），不是原始 JSON
  5) 批量接口保序、空输入不发请求
  6) llm 模式不受影响：没 key 时仍是 local 降级
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from galtl import msmt  # noqa: E402
from galtl.config import Config, DEFAULTS  # noqa: E402
from galtl.pipeline import Engine  # noqa: E402
from galtl.store import Store  # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'OK  ' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


class Azure(BaseHTTPRequestHandler):
    """假 Azure 翻译器：记录请求，按 status 返回。"""

    status = 200
    calls: list[dict] = []

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"[]")
        Azure.calls.append({
            "path": self.path,
            "key": self.headers.get("Ocp-Apim-Subscription-Key"),
            "region": self.headers.get("Ocp-Apim-Subscription-Region"),
            "body": body,
        })
        if Azure.status != 200:
            self.send_response(Azure.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"code":400000,"message":"fake"}}')
            return
        out = [{"translations": [{"text": f"译文{i + 1}", "to": "zh-Hans"}]}
               for i, _ in enumerate(body)]
        raw = json.dumps(out, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_a):
        pass


def make_engine(tmp: Path, patch: dict) -> Engine:
    cfg = Config(tmp / "config.json")
    cfg.data.update(patch)
    return Engine(cfg, Store(tmp / "cache.db"), __import__("queue").Queue(maxsize=64))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="galtl_engine_"))
    srv = HTTPServer(("127.0.0.1", 0), Azure)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"

    # 1) 默认值与判定
    check("默认引擎是 llm", DEFAULTS.get("translate_engine") == "llm",
          str(DEFAULTS.get("translate_engine")))
    check("默认带 ms_endpoint", bool(DEFAULTS.get("ms_endpoint")))
    for kind, want in (("llm", "llm"), ("ms", "ms"), ("microsoft", "ms"),
                       ("Azure", "ms"), ("", "llm"), ("whatever", "llm")):
        e = make_engine(tmp, {"translate_engine": kind})
        check(f"engine_kind({kind!r}) -> {want}", e.engine_kind == want, e.engine_kind)

    # 2) ms 模式没 key：本地降级，不请求
    Azure.calls.clear()
    e = make_engine(tmp, {"translate_engine": "ms", "ms_key": "",
                          "ms_endpoint": base, "use_cache": False})
    res = e.translate("こんな時間まで起きているなんて")
    check("ms 无 key 走本地降级",
          (res.get("meta") or {}).get("engine") == "local:no-ms-key",
          str(res.get("meta")))
    check("ms 无 key 不发请求", not Azure.calls, str(Azure.calls))

    # 3) ms 模式有 key：真发 HTTP
    Azure.calls.clear()
    e = make_engine(tmp, {"translate_engine": "ms", "ms_key": "test-key",
                          "ms_region": "eastasia", "ms_endpoint": base,
                          "use_cache": False})
    t0 = time.perf_counter()
    res = e.translate("こんな時間まで起きているなんて")
    dt = (time.perf_counter() - t0) * 1000
    call = Azure.calls[0] if Azure.calls else {}
    check("ms 有 key 走 ms 引擎", (res.get("meta") or {}).get("engine") == "ms",
          str(res.get("meta")))
    check("ms 译文回填", res.get("zh") == "译文1", repr(res.get("zh")))
    check("ms 语法来自本地库", bool(res.get("grammar")), str(res.get("grammar"))[:60])
    check("ms 生词来自本地库", isinstance(res.get("vocab"), list))
    check("请求路径带 api-version/from/to",
          "api-version=3.0" in call.get("path", "") and "from=ja" in call.get("path", "")
          and "to=zh-Hans" in call.get("path", ""), call.get("path", ""))
    check("请求带订阅 key 头", call.get("key") == "test-key", str(call.get("key")))
    check("请求带区域头", call.get("region") == "eastasia", str(call.get("region")))
    check("请求体是 [{Text:...}]",
          call.get("body") == [{"Text": "こんな時間まで起きているなんて"}],
          str(call.get("body")))
    print(f"     （ms 一轮 {dt:.0f}ms）")

    # 4) 区域留空时不发区域头
    Azure.calls.clear()
    e = make_engine(tmp, {"translate_engine": "ms", "ms_key": "k", "ms_region": "",
                          "ms_endpoint": base, "use_cache": False})
    e.translate("おはよう")
    check("区域留空则不发区域头", Azure.calls and Azure.calls[0]["region"] is None,
          str(Azure.calls[:1]))

    # 5) 错误码翻译成人话
    for code, needle in ((401, "401"), (429, "429")):
        Azure.status = code
        Azure.calls.clear()
        e = make_engine(tmp, {"translate_engine": "ms", "ms_key": "k",
                              "ms_endpoint": base, "use_cache": False})
        try:
            e.translate("テスト")
            check(f"{code} 应抛错", False, "没抛")
        except RuntimeError as exc:
            msg = str(exc)
            check(f"{code} 错误消息可读", needle in msg and "微软翻译" in msg, msg[:80])
        Azure.status = 200

    # 6) 批量与空输入
    Azure.calls.clear()
    out = msmt.translate_batch(["a", "b"], key="k", endpoint=base)
    check("批量保序", out == ["译文1", "译文2"], str(out))
    check("批量只发一次", len(Azure.calls) == 1, str(len(Azure.calls)))
    Azure.calls.clear()
    check("空输入直接返回空", msmt.translate_batch([], key="k", endpoint=base) == [])
    check("空输入不发请求", not Azure.calls)
    check("单句空白返回空串", msmt.translate("   ", key="k", endpoint=base) == "")

    # 7) llm 模式没 key 仍是原来的本地降级
    e = make_engine(tmp, {"translate_engine": "llm", "api_key": "", "use_cache": False})
    res = e.translate("おはよう")
    check("llm 无 key 本地降级", (res.get("meta") or {}).get("engine") == "local",
          str(res.get("meta")))

    srv.shutdown()
    print()
    if FAILS:
        print(f"FAILED {len(FAILS)} 项：{FAILS}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
