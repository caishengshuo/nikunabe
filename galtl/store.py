"""SQLite 台词缓存：同一句话只花一次钱，重复台词瞬时返回。"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from .paths import DB_PATH
from .textutil import dedupe_key

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

    def get(self, jp: str, ttl_days: int = 0) -> dict | None:
        key = dedupe_key(jp)
        if not key:
            return None
        with self._lock:
            try:
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
                self._db().execute(
                    "INSERT INTO lines(key,jp,payload,ts,hits) VALUES(?,?,?,?,1) "
                    "ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, ts=excluded.ts",
                    (key, jp, json.dumps(body, ensure_ascii=False), int(time.time())),
                )
                self._db().commit()
            except Exception as exc:
                log.debug("缓存写失败：%s", exc)

    def stats(self) -> dict:
        with self._lock:
            try:
                total = self._db().execute("SELECT COUNT(*) FROM lines").fetchone()[0]
                hits = self._db().execute("SELECT COALESCE(SUM(hits),0) FROM lines").fetchone()[0]
                return {"lines": int(total), "replays": int(hits)}
            except Exception:
                return {"lines": 0, "replays": 0}

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
