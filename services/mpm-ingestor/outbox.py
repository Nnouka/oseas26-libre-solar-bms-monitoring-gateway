"""Durable queue between MQTT and MPM (store-and-forward).

Survives restarts and MPM/network outages. Summaries are coalesced (only the latest
per device matters); events are kept in order and never merged.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable

MAX_BACKOFF_S = 300.0


def backoff_s(attempts: int) -> float:
    return min(MAX_BACKOFF_S, 2.0 ** attempts)


class Outbox:
    def __init__(self, path: str, max_rows: int = 10_000, clock: Callable[[], float] = time.time):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._clock = clock
        self._max_rows = max_rows
        with self._lock, self._db:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS outbox ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, device_id TEXT NOT NULL,"
                " payload TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,"
                " next_try REAL NOT NULL DEFAULT 0, created REAL NOT NULL)"
            )

    def add_summary(self, device_id: str, payload: dict) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM outbox WHERE kind='summary' AND device_id=?", (device_id,))
            self._insert("summary", device_id, payload)

    def add_event(self, device_id: str, payload: dict) -> None:
        with self._lock, self._db:
            self._insert("event", device_id, payload)
            self._trim()

    def due(self, kind: str, limit: int) -> list[tuple[int, dict]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, payload FROM outbox WHERE kind=? AND next_try<=? ORDER BY id LIMIT ?",
                (kind, self._clock(), limit),
            ).fetchall()
        return [(row_id, json.loads(payload)) for row_id, payload in rows]

    def done(self, ids: list[int]) -> None:
        with self._lock, self._db:
            self._db.executemany("DELETE FROM outbox WHERE id=?", [(i,) for i in ids])

    def failed(self, ids: list[int]) -> None:
        """Schedule a retry with exponential backoff per row."""
        with self._lock, self._db:
            for row_id in ids:
                row = self._db.execute("SELECT attempts FROM outbox WHERE id=?", (row_id,)).fetchone()
                if row is None:
                    continue
                attempts = row[0] + 1
                self._db.execute("UPDATE outbox SET attempts=?, next_try=? WHERE id=?",
                                 (attempts, self._clock() + backoff_s(attempts), row_id))

    def depth(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT kind, COUNT(*) FROM outbox GROUP BY kind").fetchall()
        counts = {"summary": 0, "event": 0}
        counts.update(dict(rows))
        return counts

    def _insert(self, kind: str, device_id: str, payload: dict) -> None:
        self._db.execute("INSERT INTO outbox (kind, device_id, payload, created) VALUES (?,?,?,?)",
                         (kind, device_id, json.dumps(payload), self._clock()))

    def _trim(self) -> None:
        """Bound disk use during very long outages: drop the oldest events first."""
        total = self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
        excess = total - self._max_rows
        if excess > 0:
            self._db.execute(
                "DELETE FROM outbox WHERE id IN (SELECT id FROM outbox WHERE kind='event' ORDER BY id LIMIT ?)",
                (excess,))
