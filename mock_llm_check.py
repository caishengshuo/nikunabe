"""用本地假模型验证「模型链路」：请求格式、JSON 容错、缓存、错误提示。

    python mock_llm_check.py

不需要任何 API Key。起一个 http://127.0.0.1:8765/v1 假服务，模拟 OpenAI 兼容返回。
"""
from __future__ import annotations

import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from galtl import llm, winapi
from galtl.config import Config
from galtl.pipeline import Engine
from galtl.store import Store

OK, BAD = "[ ok ]", "[ !! ]"
PORT = 8765

FAKE = {
    "noise": False,
    "jp": "こんな時間まで起きているなんて、明日の朝が心配だよ",
    "kana": "こんなじかんまでおきているなんて、あしたのあさがしんぱいだよ",
    "zh": "居然熬到这么晚，明早可真让人担心啊。",
    "literal": "竟然一直醒着到这种时间，明天的早晨很让人担心哦",
    "grammar": [
        {"pattern": "〜まで", "level": "N5", "meaning": "到…为止／甚至连…",
         "explain": "表示时间或范围的终点，这里是「熬到这种时间」。", "form": "時間まで"},
        {"pattern": "〜なんて", "level": "N3", "meaning": "竟然…",
         "explain": "提示意外与感叹，带责怪又关心的语气。", "form": "起きているなんて"},
    ],
    "vocab": [
        {"word": "心配", "reading": "しんぱい", "meaning": "担心"},
        {"word": "起きる", "reading": "おきる", "meaning": "起床／醒着"},
    ],
    "notes": "语气是半埋怨半关心，翻译保留「啊/哦」的亲近感。",
}

STATE = {"calls": 0, "auth": "", "payload": None, "mode": "ok"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a):  # 静默
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        STATE["calls"] += 1
        STATE["auth"] = self.headers.get("Authorization", "")
        try:
            STATE["payload"] = json.loads(raw)
        except Exception:
            STATE["payload"] = None

        if STATE["mode"] == "401":
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"invalid api key"}}')
            return
        if STATE["mode"] == "garbage":
            content = "好的，我来分析：\n```json\n" + json.dumps(FAKE, ensure_ascii=False) + ",\n```\n希望有帮助"
        elif STATE["mode"] == "empty":
            content = json.dumps({"zh": "", "literal": "", "grammar": [], "vocab": [], "notes": ""},
                                 ensure_ascii=False)
        else:
            content = json.dumps(FAKE, ensure_ascii=False)

        body = json.dumps(
            {"choices": [{"message": {"content": content}}],
             "usage": {"total_tokens": 321}},
            ensure_ascii=False,
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    winapi.set_dpi_aware()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"{OK} 假模型服务 http://127.0.0.1:{PORT}/v1")

    cfg = Config()
    cfg.set("base_url", f"http://127.0.0.1:{PORT}/v1")
    cfg.set("model", "mock-1")
    cfg.set("api_key", "sk-test-123")
    cfg.set("use_cache", False)

    kw = cfg.llm_kwargs()
    t0 = time.perf_counter()
    res = llm.analyze_text("こんな時間まで起きているなんて、明日の朝が心配だよ", kw,
                           context=["先輩、まだ起きてるの？"], glossary="先輩=学长")
    ms = (time.perf_counter() - t0) * 1000
    assert STATE["auth"] == "Bearer sk-test-123", STATE["auth"]
    print(f"{OK} 请求 → {STATE['payload']['model']} "
          f"json_mode={'response_format' in STATE['payload']} "
          f"msg={len(STATE['payload']['messages'])} 条 {ms:.0f}ms")
    print(f"{OK} 解析 zh={res.get('zh')!r} 语法 {len(res.get('grammar') or [])} 条 "
          f"词 {len(res.get('vocab') or [])} 条")

    # 容错：模型在 JSON 外卖套了废话 + 代码块 + 尾逗号
    STATE["mode"] = "garbage"
    res2 = llm.analyze_text("テスト", kw)
    print(f"{OK} 容错解析 zh={res2.get('zh')!r}")

    # 错误：401
    STATE["mode"] = "401"
    try:
        llm.analyze_text("テスト", kw)
        print(f"{BAD} 401 没有抛错")
    except llm.LlmError as exc:
        print(f"{OK} 401 正确报错：{exc}")

    # 管线 + 缓存
    STATE["mode"] = "ok"
    cfg.set("use_cache", True)
    store = Store()
    store.clear()
    events: "queue.Queue" = queue.Queue()
    eng = Engine(cfg, store, events)
    try:
        r1 = eng.translate("こんな時間まで起きているなんて、明日の朝が心配だよ")
        r2 = eng.translate("こんな時間まで起きているなんて、明日の朝が心配だよ")
        print(f"{OK} 首次 engine={r1['meta']['engine']} {r1['meta']['ms']}ms；"
              f"二次 cached={bool(r2['meta'].get('cached'))} engine={r2['meta']['engine']}")
        print(f"{OK} store 统计 {store.stats()}")

        # 译文为空时绝不能入库：否则下次命中只剩语法/生词、中文一片空白
        # （实测「深度求索中,用吋10分46秒・・・」这类界面行就是这样被写进缓存的）
        STATE["mode"] = "empty"
        jp = "テスト用の空返答"
        r3 = eng.translate(jp)
        cached = store.get(jp, 0) if hasattr(store, "get") else None
        if cached is None:
            print(f"{OK} 空译文不入库（zh={r3.get('zh')!r}）")
        else:
            print(f"{BAD} 空译文被写进缓存：{cached}")
    finally:
        eng.close()
        store.close()
        srv.shutdown()

    print("\n模型链路通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
