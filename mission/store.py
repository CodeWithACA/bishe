from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .models import MissionState, MissionStatus


class MissionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        with self._connection:
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS missions (
                    mission_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )"""
            )
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS mission_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL
                )"""
            )
            rows = self._connection.execute(
                "SELECT mission_id, payload FROM missions WHERE status = ?",
                (MissionStatus.EXECUTING,),
            ).fetchall()
            for row in rows:
                payload = json.loads(row["payload"])
                payload["status"] = MissionStatus.FAILED
                payload["error"] = "服务重启时任务仍在执行；为安全起见已中止，请重新规划。"
                payload["updated_at"] = time.time()
                self._connection.execute(
                    "UPDATE missions SET status = ?, payload = ?, updated_at = ? WHERE mission_id = ?",
                    (MissionStatus.FAILED, json.dumps(payload, ensure_ascii=False), payload["updated_at"], row["mission_id"]),
                )

    def save(self, state: MissionState) -> MissionState:
        payload = state.model_dump(mode="json")
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO missions(mission_id, status, payload, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(mission_id) DO UPDATE SET
                     status=excluded.status, payload=excluded.payload, updated_at=excluded.updated_at""",
                (state.mission_id, state.status, json.dumps(payload, ensure_ascii=False), state.created_at, state.updated_at),
            )
        return state

    def get(self, mission_id: str) -> MissionState | None:
        with self._lock:
            row = self._connection.execute("SELECT payload FROM missions WHERE mission_id = ?", (mission_id,)).fetchone()
        return MissionState.model_validate(json.loads(row["payload"])) if row else None

    def list_recent(self, limit: int = 20) -> list[MissionState]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM missions ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 100)),)
            ).fetchall()
        return [MissionState.model_validate(json.loads(row["payload"])) for row in rows]

    def append_event(self, mission_id: str, event_type: str, payload: dict[str, Any]) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO mission_events(mission_id, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
                (mission_id, event_type, json.dumps(payload, ensure_ascii=False), time.time()),
            )

    def events(self, mission_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT event_type, payload, created_at FROM mission_events WHERE mission_id = ? ORDER BY id",
                (mission_id,),
            ).fetchall()
        return [
            {"type": row["event_type"], "payload": json.loads(row["payload"]), "created_at": row["created_at"]}
            for row in rows
        ]
