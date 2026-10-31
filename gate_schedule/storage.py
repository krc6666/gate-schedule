from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class LocalStateStore:
    """Persist one complete local state snapshot without owning business rules."""

    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS app_state (key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )

    def load_or_initialize(self, default_state: dict) -> dict:
        with self._connection() as connection:
            row = connection.execute("SELECT payload FROM app_state WHERE key = 'current'").fetchone()
            if row is None:
                payload = json.dumps(default_state, ensure_ascii=False, separators=(",", ":"))
                connection.execute("INSERT INTO app_state(key, payload) VALUES ('current', ?)", (payload,))
                return json.loads(payload)
        return json.loads(row[0])

    def save(self, state: dict) -> None:
        payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO app_state(key, payload) VALUES ('current', ?) "
                "ON CONFLICT(key) DO UPDATE SET payload = excluded.payload",
                (payload,),
            )

    def backup(self) -> dict:
        with self._connection() as connection:
            row = connection.execute("SELECT payload FROM app_state WHERE key = 'current'").fetchone()
        if row is None:
            raise ValueError("本机状态尚未初始化")
        return {"formatVersion": 1, "state": json.loads(row[0])}

    def read_document(self, key: str) -> dict | None:
        with self._connection() as connection:
            row = connection.execute("SELECT payload FROM app_state WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def compare_and_save(self, key: str, expected_revision: int, state: dict) -> None:
        """Check revision and write in the same SQLite transaction."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload FROM app_state WHERE key = ?", (key,)).fetchone()
            current = json.loads(row[0]) if row else {"revision": 0}
            if current.get("revision") != expected_revision:
                raise ValueError("数据已经更新，请重新读取；本地草稿未被覆盖")
            connection.execute(
                "INSERT INTO app_state(key,payload) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload",
                (key, json.dumps(state, ensure_ascii=False)),
            )

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.database_path)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()
