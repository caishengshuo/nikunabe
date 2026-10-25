"""把 CHANGELOG.md 的某一节同步到 GitHub Release 的说明里。

用法：
    python release_notes.py            # 真改（PATCH 到 GitHub）
    python release_notes.py --dry      # 只打印要写什么，不联网改
    python release_notes.py v1.1.9     # 只处理某个 tag

为什么需要它：workflow 里原本用 `body_path: 使用说明.txt` 当 Release 说明，
于是**每个 release 的正文都是同一份使用说明**，更新日志在 GitHub 上看不到。
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHANGELOG = ROOT / "CHANGELOG.md"
OWNER_REPO = "caishengshuo/nikunabe"
TOKEN_FILE = Path(r"C:\Users\21298\AppData\Local\Temp\gh_token.txt")

FOOTER = """---

**下载**：解压 `galgame-jp-helper-v{ver}-win64.zip`，双击 `galgame日语助手.exe`（不用装 Python）。
**第一次用**：按 **F7** 框住游戏对话框的文字区，**F8** 开自动翻译；详见
[README](https://github.com/{repo}#三步上手)。
**完整更新日志**：[CHANGELOG.md](https://github.com/{repo}/blob/main/CHANGELOG.md)
"""


def read_token() -> str:
    env = (Path.home() / ".gh_token").exists()
    for p in (TOKEN_FILE,):
        if p.exists():
            t = p.read_text(encoding="utf-8", errors="replace").strip()
            if t:
                return t
    raise SystemExit("找不到 token（期望 %s）%s" % (TOKEN_FILE, " 或环境变量 GITHUB_TOKEN" if not env else ""))


def parse_changelog() -> dict[str, tuple[str, str]]:
    """{版本: (标题, 正文)}，标题是「— 」后面的那段。"""
    text = CHANGELOG.read_text(encoding="utf-8", errors="replace")
    out: dict[str, tuple[str, str]] = {}
    cur_ver = None
    cur_title = ""
    buf: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^##\s+v?(\d+\.\d+\.\d+)\s*(?:[—–-]\s*(.*))?$", line.strip())
        if m:
            if cur_ver:
                out[cur_ver] = (cur_title, "\n".join(buf).strip())
            cur_ver, cur_title, buf = m.group(1), (m.group(2) or "").strip(), []
            continue
        if cur_ver is not None:
            buf.append(line)
    if cur_ver:
        out[cur_ver] = (cur_title, "\n".join(buf).strip())
    return out


def api(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    url = "https://api.github.com" + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("User-Agent", "nikunabe-release-notes")
    req.add_header("Accept", "application/vnd.github+json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:400]
        raise SystemExit("HTTP %s %s %s -> %s" % (e.code, method, path, body))


def main(argv: list[str]) -> int:
    dry = "--dry" in argv
    only = next((a for a in argv[1:] if not a.startswith("-")), None)
    sections = parse_changelog()
    if not sections:
        raise SystemExit("CHANGELOG.md 里没解析到任何 `## x.y.z` 小节")

    token = "" if dry else read_token()
    releases = [] if dry else api("GET", "/repos/%s/releases?per_page=50" % OWNER_REPO, token)
    if dry:
        # dry 模式下也要知道有哪些 tag：直接看本地 git tag
        import subprocess
        tags = subprocess.run(["git", "tag"], cwd=str(ROOT), capture_output=True, text=True).stdout.split()
        releases = [{"tag_name": t, "id": 0, "body": ""} for t in tags if t.startswith("v")]

    changed = 0
    for rel in releases:
        tag = str(rel.get("tag_name") or "")
        if only and tag != only:
            continue
        ver = tag.lstrip("v")
        if ver not in sections:
            print("跳过 %-8s （CHANGELOG 里没有这一版）" % tag)
            continue
        title, body = sections[ver]
        name = "v%s — %s" % (ver, title) if title else tag
        note = "## 这个版本改了什么\n\n%s\n\n%s" % (
            body, FOOTER.format(ver=ver, repo=OWNER_REPO)
        )
        if dry:
            print("=" * 70)
            print("TAG ", tag, "→ 标题:", name)
            print(note[:900] + ("\n...(共 %d 字)" % len(note) if len(note) > 900 else ""))
        else:
            api("PATCH", "/repos/%s/releases/%d" % (OWNER_REPO, int(rel["id"])),
                token, {"name": name, "body": note})
            print("已更新 %-8s %s（%d 字）" % (tag, name, len(note)))
        changed += 1

    print("\n共处理 %d 个 release" % changed)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main(sys.argv))
