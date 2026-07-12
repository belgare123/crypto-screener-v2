"""
Settings DB — per-user настройки для Telegram бота.

v0.9.0:
  - aiosqlite с постоянным соединением (без open/close на каждый запрос)
  - close() для graceful shutdown
  - Threading.Lock заменён на asyncio.Lock
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

try:
    import aiosqlite
    HAS_AIOSQLITE = True
except ImportError:
    HAS_AIOSQLITE = False

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
    """SQLite-хранилище настроек для каждого chat_id (async, aiosqlite)."""

    def __init__(self, db_path: str | Path):
        self._path = Path(db_path)
        self._conn: aiosqlite.Connection | None = None

    async def start(self):
        """Открыть соединение (вызывается при старте)."""
        if not HAS_AIOSQLITE:
            logger.error("aiosqlite not installed — UserSettingsDB disabled")
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._path))
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                chat_id     INTEGER PRIMARY KEY,
                settings    TEXT NOT NULL DEFAULT '{}',
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await self._conn.commit()
        logger.info("UserSettingsDB ready at %s (async)", self._path)

    async def close(self):
        """Закрыть соединение (при shutdown)."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("UserSettingsDB closed")

    async def get(self, chat_id: int) -> dict[str, Any]:
        defaults = dict(DEFAULT_SETTINGS)
        if not self._conn:
            return defaults
        row = await self._conn.execute_fetchall(
            "SELECT settings FROM user_settings WHERE chat_id = ?",
            (chat_id,),
        )
        if row:
            saved = json.loads(row[0][0])
            defaults.update(saved)
        return defaults

    async def set(self, chat_id: int, key: str, value: Any) -> dict[str, Any]:
        current = await self.get(chat_id)
        current[key] = value
        if self._conn:
            await self._conn.execute(
                """INSERT INTO user_settings (chat_id, settings, updated_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(chat_id) DO UPDATE SET
                       settings = excluded.settings,
                       updated_at = datetime('now')""",
                (chat_id, json.dumps(current)),
            )
            await self._conn.commit()
        return current

    async def get_all(self) -> list[dict[str, Any]]:
        """Все пользователи и их настройки (для статистики)."""
        if not self._conn:
            return []
        rows = await self._conn.execute_fetchall(
            "SELECT chat_id, settings FROM user_settings"
        )
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
