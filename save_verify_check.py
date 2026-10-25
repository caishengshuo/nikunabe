"""「保存并应用」后的配置校验回归。

覆盖 SettingsDialog._verify 的六种结果，以及 _save→后台线程→弹窗→关窗 这条链路：

  1) ms 模式没填 key            → warn（说明只走本地句型库）
  2) ms 模式 key 有效（假 Azure）→ ok（弹窗里带真实译文）
  3) ms 模式 key 被拒（401）     → error（消息里能照做）
  4) llm 模式没填 key            → warn
  5) llm 模式 base_url 为空       → error
  6) 配置明显不合理（region 零面积 / poll_ms 太小）→ 结果里带提醒
  7) _save(verify=True) 真跑一遍：配置落盘 + 弹窗被调用 + 窗口关闭
     （把 messagebox 换成记录器，不会真的卡住等用户点确定）

跑法：python save_verify_check.py，全过 exit=0。
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import tkinter as tk
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tkinter import messagebox

sys.path.insert(0, str(Path(__file__).resolve().parent))

from galtl.config import Config  # noqa: E402
from galtl.ui_settings import SettingsDialog  # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'OK  ' if ok else 'FAIL'} {name}" + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


class Azure(BaseHTTPRequestHandler):
    status = 200
    hits = 0

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"[]")
        Azure.hits += 1
        if Azure.status != 200:
            self.send_response(Azure.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"code":401001,"message":"bad key"}}')
            return
        out = [{"translations": [{"text": f"别慌，还来得及。({len(body)})", "to": "zh-Hans"}]}]
        raw = json.dumps(out, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_a):
        pass


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="galtl_verify_"))
    srv = HTTPServer(("127.0.0.1", 0), Azure)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"

    root = tk.Tk()
    root.geometry("200x120+60+60")
    root.update()

    def fresh(**patch) -> SettingsDialog:
        cfg = Config(tmp / f"c{time.time_ns()}.json")
        cfg.data.update(patch)
        return SettingsDialog(root, cfg, on_saved=None)

    # 1) ms 没 key -> warn
    d = fresh(translate_engine="ms", ms_key="", region=[10, 10, 800, 120])
    lvl, title, body = d._verify("ms")
    check("ms 无 key -> warn", lvl == "warn", f"{lvl} {title}")
    check("ms 无 key 的说明提到本地句型库", "本地句型库" in body, body[:60])
    d._close()

    # 2) ms 有 key（假 Azure）-> ok，带译文
    Azure.status, Azure.hits = 200, 0
    d = fresh(translate_engine="ms", ms_key="k", ms_endpoint=base,
              region=[10, 10, 800, 120])
    lvl, title, body = d._verify("ms")
    check("ms 有 key -> ok", lvl == "ok", f"{lvl} {title}")
    check("ok 的弹窗里带真实译文", "别慌" in body, body[:80])
    check("ok 的弹窗里写了区域", "区域" in body, body[:80])
    check("ms 校验真的发了请求", Azure.hits == 1, str(Azure.hits))
    d._close()

    # 3) ms key 被拒 -> error 且可照做
    Azure.status, Azure.hits = 401, 0
    d = fresh(translate_engine="ms", ms_key="bad", ms_endpoint=base,
              region=[10, 10, 800, 120])
    lvl, title, body = d._verify("ms")
    check("ms 401 -> error", lvl == "error", f"{lvl} {title}")
    check("error 里带 401 与门户指引", "401" in body and "KEY 1" in body, body[:100])
    Azure.status = 200
    d._close()

    # 4) llm 没 key -> warn
    d = fresh(translate_engine="llm", api_key="", base_url="https://x/v1",
              model="m", region=[10, 10, 800, 120])
    lvl, title, body = d._verify("llm")
    check("llm 无 key -> warn", lvl == "warn", f"{lvl} {title}")
    d._close()

    # 5) llm base_url 为空 -> error
    d = fresh(translate_engine="llm", api_key="k", base_url="", model="m",
              region=[10, 10, 800, 120])
    lvl, title, body = d._verify("llm")
    check("llm 空 base_url -> error", lvl == "error", f"{lvl} {title}")
    check("error 提示到 /v1 结尾", "/v1" in body, body[:80])
    d._close()

    # 6) 不合理配置 -> 提醒进结果
    d = fresh(translate_engine="ms", ms_key="", region=[0, 0, 0, 0], poll_ms=60)
    lvl, title, body = d._verify("ms")
    check("region 零面积被提醒", "F7" in body, body[:120])
    check("poll_ms 太小被提醒", "间隔" in body, body[:120])
    d._close()

    # 7) _save(verify=True) 全链路：落盘 + 弹窗 + 关窗
    cfg = Config(tmp / "c_full.json")
    cfg.data.update({"translate_engine": "ms", "ms_key": "k", "ms_endpoint": base,
                     "region": [10, 10, 800, 120]})
    seen: list[tuple[str, str]] = []
    orig = (messagebox.showinfo, messagebox.showwarning, messagebox.showerror)
    messagebox.showinfo = lambda t, m, **k: seen.append(("info", t))
    messagebox.showwarning = lambda t, m, **k: seen.append(("warn", t))
    messagebox.showerror = lambda t, m, **k: seen.append(("error", t))
    try:
        d = SettingsDialog(root, cfg, on_saved=None)
        d.var_engine.set("ms")
        d._save(verify=True)
        check("保存后立刻禁用按钮", bool(d.btn_save.instate(["disabled"])),
              str(d.btn_save.state()))
        deadline = time.time() + 25
        while time.time() < deadline and not seen:
            root.update()
            time.sleep(0.05)
        check("弹窗被调用", bool(seen), str(seen))
        check("弹的是成功提示", bool(seen) and seen[0][0] == "info", str(seen))
        check("窗口在校验后关闭", not d.win.winfo_exists())
        saved = json.loads((tmp / "c_full.json").read_text(encoding="utf-8"))
        check("配置真的落盘", saved.get("translate_engine") == "ms",
              str(saved.get("translate_engine")))
    finally:
        messagebox.showinfo, messagebox.showwarning, messagebox.showerror = orig

    srv.shutdown()
    try:
        root.destroy()
    except tk.TclError:
        pass
    print()
    if FAILS:
        print(f"FAILED {len(FAILS)} 项：{FAILS}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
