"""SQLite 台词缓存：同一句话只花一次钱，重复台词瞬时返回。"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from .paths import DB_PATH
from .textutil import dedupe_key, similar_key, stable_key

log = logging.getLogger("galtl.store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lines (
  key   TEXT PRIMARY KEY,
  jp    TEXT NOT NULL,
  payload TEXT NOT NULL,
  ts    INTEGER NOT NULL,
  hits  INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_lines_ts ON lines(ts);
CREATE TABLE IF NOT EXISTS aliases (
  key       TEXT PRIMARY KEY,
  canonical TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        return self._conn

    def _canon(self, key: str, _depth: int = 0) -> str:
        """把一个「读法」解析到它的正身键（没登记别名就是它自己）。

        别名可能连着好几跳（A→B→C：先登记的 B 后来自己也被并进了 C），所以要一路
        跟到底；否则 `get()` 会停在一个已经被删掉的键上，查不到任何译文。
        """
        if _depth > 8:
            return key
        try:
            row = self._db().execute(
                "SELECT canonical FROM aliases WHERE key=?", (key,)
            ).fetchone()
        except Exception:
            return key
        if not row or not row[0] or row[0] == key:
            return key
        return self._canon(row[0], _depth + 1)

    def _alias(self, key: str, canonical: str) -> None:
        """登记「这个读法就是那句话」。调用方需持锁。"""
        if key == canonical:
            return
        try:
            self._db().execute(
                "INSERT INTO aliases(key,canonical) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET canonical=excluded.canonical",
                (key, canonical),
            )
        except Exception as exc:
            log.debug("别名写入失败：%s", exc)

    def get(self, jp: str, ttl_days: int = 0) -> dict | None:
        key = dedupe_key(jp)
        if not key:
            return None
        with self._lock:
            try:
                key = self._canon(key)
                cur = self._db().execute(
                    "SELECT payload, ts FROM lines WHERE key=?", (key,)
                )
                row = cur.fetchone()
                if not row:
                    return None
                payload_raw, ts = row
                if ttl_days and (time.time() - ts) > ttl_days * 86400:
                    return None
                self._db().execute("UPDATE lines SET hits=hits+1 WHERE key=?", (key,))
                self._db().commit()
                data = json.loads(payload_raw)
                data["cached"] = True
                return data
            except Exception as exc:
                log.debug("缓存读失败：%s", exc)
                return None

    def put(self, jp: str, payload: dict) -> None:
        key = dedupe_key(jp)
        if not key:
            return
        body = dict(payload)
        body.pop("cached", None)
        with self._lock:
            try:
                canon = self._canon(key)
                if canon != key:
                    # 这一句已经有正身：只续命，不再存第二份译文。
                    # 否则同一句的两套译文会同时留在库里，OCR 读成哪个就用哪个，
                    # 屏幕上就是两句话来回闪。
                    self._db().execute(
                        "UPDATE lines SET ts=? WHERE key=?", (int(time.time()), canon)
                    )
                    self._db().commit()
                    return
                exists = self._db().execute(
                    "SELECT 1 FROM lines WHERE key=?", (key,)
                ).fetchone()
                if not exists:
                    near = self._find_similar_locked(jp, key)
                    if near:
                        self._db().execute(
                            "UPDATE lines SET ts=?, hits=hits+1 WHERE key=?",
                            (int(time.time()), near),
                        )
                        self._alias(key, near)
                        self._db().commit()
                        return
                self._db().execute(
                    "INSERT INTO lines(key,jp,payload,ts,hits) VALUES(?,?,?,?,1) "
                    "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, ts=excluded.ts",
                    (key, jp, json.dumps(body, ensure_ascii=False), int(time.time())),
                )
                self._db().commit()
            except Exception as exc:
                log.debug("缓存写失败：%s", exc)

    def delete(self, jp: str) -> None:
        """删掉一条缓存（命中发现是脏数据时用，比如早期存进去的「我无法翻译」）。"""
        key = dedupe_key(jp)
        if not key:
            return
        with self._lock:
            try:
                self._db().execute("DELETE FROM lines WHERE key=?", (key,))
                self._db().commit()
            except Exception as exc:
                log.debug("缓存删失败：%s", exc)

    def purge(self, predicate) -> int:
        """按谓词清理缓存，返回删掉的行数（升级后一次性洗掉历史脏数据用）。"""
        removed = 0
        with self._lock:
            try:
                rows = self._db().execute("SELECT jp,payload FROM lines").fetchall()
                for jp, payload in rows:
                    try:
                        data = json.loads(payload)
                    except Exception:
                        data = {}
                    try:
                        bad = bool(predicate(jp, data))
                    except Exception:
                        bad = False
                    if bad:
                        self._db().execute("DELETE FROM lines WHERE jp=?", (jp,))
                        removed += 1
                if removed:
                    self._db().commit()
            except Exception as exc:
                log.debug("缓存清理失败：%s", exc)
        return removed

    def stats(self) -> dict:
        with self._lock:
            try:
                total = self._db().execute("SELECT COUNT(*) FROM lines").fetchone()[0]
                hits = self._db().execute("SELECT COALESCE(SUM(hits),0) FROM lines").fetchone()[0]
                return {"lines": int(total), "replays": int(hits)}
            except Exception:
                return {"lines": 0, "replays": 0}

    def _find_similar_locked(self, jp: str, key: str, limit: int = 400) -> str | None:
        """在最近的缓存里找「同一句台词的另一种读法」，返回它的正身键。调用方需持锁。"""
        if len(key) < 4:
            return None
        try:
            rows = self._db().execute(
                "SELECT key FROM lines ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        except Exception:
            return None
        for (old_key,) in rows:
            if old_key == key or not similar_key(key, old_key):
                continue
            return old_key
        return None

    def find_similar(self, jp: str, limit: int = 400) -> dict | None:
        """在最近几百条缓存里找「同一句台词的另一种读法」，命中就返回它的结果。

        为什么要走库而不只看内存：同一句台词在重启程序后仍会被 OCR 读成别的样子
        （句首被对话框边缘糊成「~・ユ」，或中段漏读），内存里的 `_recent` 已经清空，
        于是又翻了一遍，屏幕上就成了同一套台词在两套译文之间来回闪。
        """
        key = dedupe_key(jp)
        with self._lock:
            canon = self._canon(key)
            if canon != key:
                near = canon
            else:
                near = self._find_similar_locked(jp, key, limit)
            if not near:
                return None
            try:
                row = self._db().execute(
                    "SELECT jp,payload,ts,hits FROM lines WHERE key=?", (near,)
                ).fetchone()
            except Exception:
                return None
        if not row:
            return None
        old_jp, payload, ts, hits = row
        try:
            data = json.loads(payload)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        data.setdefault("jp", old_jp)
        data["ts"] = ts
        data["hits"] = hits
        data["similar_key"] = near
        return data

    def compact(self, limit: int = 4000) -> dict:
        """把库里「同一句台词的多种读法」合并成一个正身，其余登记成别名删掉。

        只在启动时跑一次成本可接受（几百行、每行一次相似判定）。合并后 OCR 无论读成
        哪个版本都会返回同一份译文，这是「两句译文来回闪」的根治办法。
        跑三趟：一趟合并会改变「谁是正身」，后面的行可能才刚变得可比较。
        """
        merged = 0
        with self._lock:
            for _ in range(3):
                try:
                    got = self._merge_once(limit)
                except Exception:
                    log.warning("缓存合并失败（不影响使用）", exc_info=True)
                    break
                merged += got
                if not got:
                    break
            try:
                self._resolve_aliases()
            except Exception:
                log.debug("别名收敛失败", exc_info=True)
        return {"merged": merged}

    def _resolve_aliases(self) -> None:
        """把所有别名改指到最终正身；正身已被删掉的别名直接丢弃。调用方需持锁。"""
        rows = self._db().execute("SELECT key, canonical FROM aliases").fetchall()
        for key, canonical in rows:
            final = self._canon(canonical)
            alive = self._db().execute(
                "SELECT 1 FROM lines WHERE key=?", (final,)
            ).fetchone()
            if not alive:
                self._db().execute("DELETE FROM aliases WHERE key=?", (key,))
            elif final != canonical:
                self._db().execute(
                    "UPDATE aliases SET canonical=? WHERE key=?", (final, key)
                )
        self._db().commit()

    def _merge_once(self, limit: int = 4000) -> int:
        """跑一趟合并，返回这趟并掉了多少行。调用方需持锁。"""
        merged = 0
        rows = self._db().execute(
            "SELECT key,jp,payload,ts,hits FROM lines ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()

        def score(payload, ts, hits):
            try:
                d = json.loads(payload) or {}
            except Exception:
                d = {}
            rich = 1 if (d.get("grammar") or d.get("vocab")) else 0
            return (rich, int(hits or 0), int(ts or 0))

        # 每个「正身」记一份 (key, jp, score)，新来的行若与某个正身相似，
        # 就把分数低的那个降级成别名（谁信息更全、被看过更多次谁当正身）。
        keep: list[tuple[str, str, tuple]] = []
        for key, jp, payload, ts, hits in rows:
            if len(key) < 4:
                continue
            mine = score(payload, ts, hits)
            hit = None
            for i, (k, _j, _s) in enumerate(keep):
                if similar_key(key, k):
                    hit = i
                    break
            if hit is None:
                keep.append((key, jp, mine))
                continue
            canon_key, canon_jp, canon_score = keep[hit]
            if mine > canon_score:
                # 新行更值得当正身：旧正身降级成别名，把它的复用次数记到新正身上
                keep[hit] = (key, jp, mine)
                self._alias(canon_key, key)
                self._db().execute(
                    "UPDATE lines SET hits=hits+? WHERE key=?", (int(hits or 0), key)
                )
                self._db().execute("DELETE FROM lines WHERE key=?", (canon_key,))
            else:
                self._alias(key, canon_key)
                self._db().execute(
                    "UPDATE lines SET hits=hits+? WHERE key=?", (int(hits or 0), canon_key)
                )
                self._db().execute("DELETE FROM lines WHERE key=?", (key,))
            merged += 1
        if merged:
            self._db().commit()
            log.info("缓存合并：%d 条重复读法并到同一句", merged)
        return merged

    def recent(self, limit: int = 20) -> list[dict]:
        with self._lock:
            try:
                rows = self._db().execute(
                    "SELECT jp,payload,ts FROM lines ORDER BY ts DESC LIMIT ?", (limit,)
                ).fetchall()
            except Exception:
                return []
        out = []
        for jp, payload, ts in rows:
            try:
                data = json.loads(payload)
            except Exception:
                data = {}
            data.setdefault("jp", jp)
            data["ts"] = ts
            out.append(data)
        return out

    def clear(self) -> None:
        with self._lock:
            try:
                self._db().execute("DELETE FROM lines")
                self._db().commit()
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                finally:
                    self._conn = None
