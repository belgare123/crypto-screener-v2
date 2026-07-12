"""Settings DB — per-user настройки для Telegram бота."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    "exchange": "BYBIT",
    "market": "FUTURE",
    "threshold_pct": 2.0,
    "timeframe": "5m",
    "min_score": 60,
    "premium": 1,
}


class UserSettingsDB:
    """SQLite-хранилище настроек для каждого chat_id (синхронное, sqlite3)."""

    def __init__(self, db_path: str | Path):
        self._path = Path(db_path)
        self._lock = Lock()
        self._init_db()

    def _init_db(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, sqlite3.connect(str(self._path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_settings (
                    chat_id     INTEGER PRIMARY KEY,
                    settings    TEXT NOT NULL DEFAULT '{}',
                    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.commit()
        logger.info("UserSettingsDB ready at %s", self._path)

    def get(self, chat_id: int) -> dict[str, Any]:
        defaults = dict(DEFAULT_SETTINGS)
        with self._lock, sqlite3.connect(str(self._path)) as conn:
            row = conn.execute(
                "SELECT settings FROM user_settings WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
        if row:
            saved = json.loads(row[0])
            defaults.update(saved)
        return defaults

    def set(self, chat_id: int, key: str, value: Any) -> dict[str, Any]:
        current = self.get(chat_id)
        current[key] = value
        with self._lock, sqlite3.connect(str(self._path)) as conn:
            conn.execute(
                """INSERT INTO user_settings (chat_id, settings, updated_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(chat_id) DO UPDATE SET
                       settings = excluded.settings,
                       updated_at = datetime('now')""",
                (chat_id, json.dumps(current)),
            )
            conn.commit()
        return current

    def get_all(self) -> list[dict[str, Any]]:
        """Все пользователи и их настройки (для статистики)."""
        with self._lock, sqlite3.connect(str(self._path)) as conn:
            rows = conn.execute(
                "SELECT chat_id, settings FROM user_settings"
            ).fetchall()
        result = []
        for chat_id, sjson in rows:
            s = json.loads(sjson)
            s["chat_id"] = chat_id
            result.append(s)
        return result


# singleton
_db: UserSettingsDB | None = None


def get_settings_db() -> UserSettingsDB | None:
    return _db


def setup_settings_db(db_path: str | Path) -> UserSettingsDB:
    global _db
    _db = UserSettingsDB(db_path)
    return _db
