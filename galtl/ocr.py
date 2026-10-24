"""Windows OCR（离线，日语引擎）客户端。

用一个常驻 PowerShell 进程承载 WinRT OCR，避免每次识别都付 ~800ms 的启动成本。
通过 stdin/stdout 行协议通信；识别一张图通常 30~120ms。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

from .paths import OCR_PS1, WORK_DIR

log = logging.getLogger("galtl.ocr")

_CREATE_NO_WINDOW = 0x08000000

_seq_lock = threading.Lock()
_seq_n = 0


def _seq() -> int:
    global _seq_n
    with _seq_lock:
        _seq_n += 1
        return _seq_n


def _write_temp_png(png: bytes) -> Path:
    """回退用：写临时 PNG。文件名唯一，绝不会因为上一个句柄没放而写不进去。"""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    path = WORK_DIR / f"s{os.getpid()}_{_seq()}.png"
    path.write_bytes(png)
    return path


class OcrUnavailable(RuntimeError):
    pass


class WinOcr:
    """常驻 OCR 引擎。线程安全（内部串行化请求）。"""

    def __init__(self, lang: str = "ja", ps1: Path = OCR_PS1):
        self.lang = lang
        self.ps1 = Path(ps1)
        self._proc: subprocess.Popen | None = None
        self._q: "queue.Queue[str]" = queue.Queue()
        self._lock = threading.Lock()
        self._closing = False  # close() 之后禁止任何重启尝试
        self._reader: threading.Thread | None = None
        self.info: dict = {}
        self._start_fail: str | None = None

    # ---------- 生命周期 ----------
    def _powershell(self) -> str:
        for cand in ("powershell.exe", "pwsh.exe"):
            try:
                subprocess.run([cand, "-NoProfile", "-Command", "exit 0"], timeout=10,
                               creationflags=_CREATE_NO_WINDOW)
                return cand
            except Exception:
                continue
        return "powershell.exe"

    def start(self, wait: float = 40.0) -> dict:
        self._closing = False
        if self._proc and self._proc.poll() is None:
            return self.info
        if not self.ps1.exists():
            raise OcrUnavailable(f"缺少 OCR 脚本：{self.ps1}")
        ps = self._powershell()
        cmd = [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-File", str(self.ps1), self.lang]
        log.info("启动 OCR worker: %s", " ".join(cmd))
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=_CREATE_NO_WINDOW,
        )
        while not self._q.empty():
            self._q.get_nowait()
        self._reader = threading.Thread(target=self._pump, name="galtl-ocr-read", daemon=True)
        self._reader.start()
        deadline = time.time() + wait
        while time.time() < deadline:
            try:
                line = self._q.get(timeout=0.5)
            except queue.Empty:
                if self._proc.poll() is not None:
                    tail = ""
                    try:
                        tail = (self._proc.stderr.read() or "")[-600:]
                    except Exception:
                        pass
                    raise OcrUnavailable(f"OCR worker 退出（code={self._proc.returncode}）{tail}")
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("ready"):
                self.info = msg
                log.info("OCR 就绪：lang=%s maxDim=%s", msg.get("lang"), msg.get("maxDim"))
                return msg
            if msg.get("ok") is False:
                err = msg.get("error")
                avail = [a for a in (msg.get("available") or []) if a]
                self.info = msg
                if err == "no_lang_pack":
                    want = msg.get("requested") or self.lang
                    raise OcrUnavailable(
                        f"这台电脑的 Windows OCR 没有「{want}」语言包（已装："
                        + ("、".join(avail) if avail else "无")
                        + "）。装法：设置 → 时间和语言 → 语言和区域 → 添加语言「日本語」→ "
                        "在它的「语言选项」里勾选「光学字符识别(OCR)」，装完重开本程序。"
                    )
                raise OcrUnavailable(msg.get("detail") or err or "OCR 初始化失败")
        raise OcrUnavailable("OCR worker 启动超时")

    def _pump(self) -> None:
        proc = self._proc
        assert proc and proc.stdout
        for line in proc.stdout:
            line = line.strip()
            if line:
                self._q.put(line)
        # 进程结束：塞一个哨兵，避免请求方永久等待
        self._q.put('{"ok":false,"error":"worker_dead"}')

    def close(self) -> None:
        self._closing = True
        proc, self._proc = self._proc, None
        if not proc:
            return
        try:
            if proc.poll() is None and proc.stdin:
                proc.stdin.write("QUIT\n")
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def alive(self) -> bool:
        return bool(self._proc and self._proc.poll() is None)

    # ---------- 识别 ----------
    def _request(self, header: str, payload: str | None, timeout: float) -> list[dict]:
        """发一条请求并等一条 JSON 回包。header/payload 都是单行文本。"""
        with self._lock:
            if self._closing:
                raise OcrUnavailable("OCR 已停止")
            if not self.alive():
                self.close()
                self.start()
            proc = self._proc  # 拿住本地引用：close() 可能在别的线程把字段置空
            if proc is None or proc.stdin is None:
                raise OcrUnavailable("OCR worker 已停止")
            while not self._q.empty():  # 丢弃陈旧消息
                self._q.get_nowait()
            try:
                proc.stdin.write(header + "\n")
                if payload is not None:
                    proc.stdin.write(payload + "\n")
                proc.stdin.flush()
            except (OSError, ValueError) as exc:
                self.close()
                raise OcrUnavailable(f"OCR worker 管道断开：{exc}") from exc
            deadline = time.time() + timeout
            while True:
                remain = deadline - time.time()
                if remain <= 0:
                    self.close()  # 卡死的 worker 直接重建
                    raise OcrUnavailable("OCR 识别超时")
                try:
                    line = self._q.get(timeout=min(remain, 1.0))
                except queue.Empty:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                if msg.get("ok"):
                    return list(msg.get("lines") or [])
                err = msg.get("error")
                detail = str(msg.get("detail") or err or "OCR 失败")
                if err in ("worker_dead", "ocr_failed"):
                    # 识别异常后 worker 内部状态可能不干净，直接重建最稳
                    self.close()
                raise OcrUnavailable(detail)

    def recognize_png(self, png: bytes, timeout: float = 25.0) -> list[dict]:
        """传入 PNG 字节，返回行列表 [{text,x,y,w,h}]。

        走内存 base64 传输：不落盘 → 不会有临时文件句柄互锁 / 写不进去的问题。
        """
        if not png:
            raise OcrUnavailable("空图片")
        b64 = base64.b64encode(png).decode("ascii")
        return self._request(f"B64 {len(b64)}", b64, timeout)

    def recognize_file(self, path: Path | str, timeout: float = 25.0) -> list[dict]:
        p = str(path)
        if not p.isascii():
            raise OcrUnavailable("OCR 图片路径必须是纯 ASCII")
        return self._request(p, None, timeout)


# ---------- 文本行归并 ----------
def merge_lines(lines: list[dict]) -> list[str]:
    """按纵向量叠聚类成「行块」，块内按 x 拼装；块按 y 排序。

    galgame 文本框通常单列，结果为 1 块；日文字幕换行会被合回同一块。
    """
    if not lines:
        return []
    prepared = []
    for ln in lines:
        t = (ln.get("text") or "").strip()
        if not t:
            continue
        y = float(ln.get("y", 0.0))
        h = float(ln.get("h", 1.0)) or 1.0
        prepared.append(
            {"text": t, "x": float(ln.get("x", 0.0)), "y": y, "h": h, "cy": y + h / 2.0}
        )
    if not prepared:
        return []
    prepared.sort(key=lambda i: (i["y"], i["x"]))

    clusters: list[dict] = []
    for item in prepared:
        placed = False
        for c in clusters:
            if c["y0"] - item["h"] * 0.35 <= item["cy"] <= c["y1"] + item["h"] * 0.35:
                c["items"].append(item)
                c["y0"] = min(c["y0"], item["y"])
                c["y1"] = max(c["y1"], item["y"] + item["h"])
                placed = True
                break
        if not placed:
            clusters.append({"y0": item["y"], "y1": item["y"] + item["h"], "items": [item]})

    clusters.sort(key=lambda c: (c["y0"], min(i["x"] for i in c["items"])))
    out: list[str] = []
    for c in clusters:
        items = sorted(c["items"], key=lambda i: i["x"])
        out.append("".join(i["text"] for i in items))
    return out


class TesseractOcr:
    """可选：Tesseract 后备引擎（需要系统安装 tesseract + jpn 数据）。"""

    name = "tesseract"

    def __init__(self, exe: str | None = None, lang: str = "jpn"):
        self.exe = exe or "tesseract"
        self.lang = lang
        self.info = {"engine": "tesseract", "lang": lang}
        self._ok: bool | None = None

    def start(self, wait: float = 5.0) -> dict:
        if not self.available():
            raise OcrUnavailable("未找到 tesseract 可执行文件")
        return self.info

    def available(self) -> bool:
        if self._ok is not None:
            return self._ok
        try:
            r = subprocess.run([self.exe, "--version"], capture_output=True, timeout=10,
                               creationflags=_CREATE_NO_WINDOW)
            self._ok = r.returncode == 0
        except Exception:
            self._ok = False
        return self._ok

    def recognize_png(self, png: bytes, timeout: float = 25.0) -> list[dict]:
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        p = WORK_DIR / f"t{os.getpid()}.png"
        p.write_bytes(png)
        r = subprocess.run(
            [self.exe, str(p), "stdout", "-l", self.lang, "--psm", "6"],
            capture_output=True, timeout=timeout, creationflags=_CREATE_NO_WINDOW,
        )
        text = r.stdout.decode("utf-8", "replace")
        return [{"text": ln, "x": 0.0, "y": float(i * 10), "w": 1.0, "h": 8.0}
                for i, ln in enumerate(text.splitlines()) if ln.strip()]

    recognize_file = recognize_png  # type: ignore[assignment]

    def close(self) -> None:
        pass

    def alive(self) -> bool:
        return True


def make_ocr(cfg: dict):
    engine = (cfg.get("ocr_engine") or "winocr").lower()
    if engine == "tesseract":
        return TesseractOcr(cfg.get("tesseract_exe"), cfg.get("tesseract_lang") or "jpn")
    return WinOcr(lang=cfg.get("ocr_lang") or "ja")
