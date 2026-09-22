"""SQLite persistence for valid templates and successful inferences.

The database lives on a mounted volume (``$CUE_DB_PATH``), so results
survive container restarts. Every write is serialized by a process lock and
runs inside an immediate transaction: failed inferences never insert a row
and cannot partially modify existing data.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS templates (
    id         TEXT PRIMARY KEY,
    document   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS inferences (
    id          TEXT PRIMARY KEY,
    template_id TEXT NOT NULL REFERENCES templates(id),
    delays      TEXT NOT NULL,
    status      TEXT NOT NULL,
    results     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_inferences_template
    ON inferences(template_id, created_at);
"""


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def insert_template(self, template_id: str, document: Dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO templates(id, document) VALUES (?, ?)",
                (template_id, json.dumps(document, ensure_ascii=False, sort_keys=True)),
            )

    def get_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, document, created_at FROM templates WHERE id = ?",
                (template_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "document": json.loads(row["document"]),
            "created_at": row["created_at"],
        }

    def insert_inference(
        self,
        inference_id: str,
        template_id: str,
        delays: Dict[str, int],
        results: List[Dict[str, Any]],
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO inferences(id, template_id, delays, status, results) "
                "VALUES (?, ?, ?, 'ok', ?)",
                (
                    inference_id,
                    template_id,
                    json.dumps(delays, ensure_ascii=False, sort_keys=True),
                    json.dumps(results, ensure_ascii=False, sort_keys=True),
                ),
            )

    def get_inference(
        self, inference_id: str
    ) -> Optional[Tuple[str, Dict[str, int], List[Dict[str, Any]], str]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT template_id, delays, results, status, created_at "
                "FROM inferences WHERE id = ?",
                (inference_id,),
            ).fetchone()
        if row is None:
            return None
        return (
            row["template_id"],
            json.loads(row["delays"]),
            json.loads(row["results"]),
            row["created_at"],
        )

    def count_inferences(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM inferences").fetchone()[0]

    def count_templates(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
