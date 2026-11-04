"""실행 기록 저장 (SQLite). 인증키·원문 응답 전체는 저장하지 않는다."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    input_text  TEXT NOT NULL,
    verdict     TEXT NOT NULL,
    company     TEXT,
    report_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC);
"""


class RunStore:
    def __init__(self, path: Path | str | None = None):
        config.ensure_dirs()
        self.path = Path(path) if path else config.DB_PATH
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def save(self, report: dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs (run_id, created_at, input_text, verdict, company, report_json)"
                " VALUES (?,?,?,?,?,?)",
                (
                    report["run_id"],
                    report["created_at"],
                    report["input_text"],
                    report["verdict"],
                    (report.get("company") or {}).get("name", ""),
                    json.dumps(report, ensure_ascii=False),
                ),
            )

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT run_id, created_at, input_text, verdict, company FROM runs"
                " ORDER BY rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT report_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return json.loads(row["report_json"]) if row else None
