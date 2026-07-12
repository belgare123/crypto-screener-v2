"""
SignalDB — async SQLite для хранения истории сигналов и расчёта win rate.

Таблицы:
  - signal_history: все отправленные сигналы с метаданными
  - (в будущем) signal_evaluations: курс входа/выхода, результат

Зависимости: aiosqlite
"""
from __future__ import annotations

import json
import logging
import time

import aiosqlite

logger = logging.getLogger(__name__)

from pathlib import Path
DB_PATH = str(Path(__file__).parent.parent / "signals.db")


class SignalDB:
    """Асинхронная обёртка над SQLite для сигналов."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def init(self):
        """Открыть соединение, создать таблицы если нет."""
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row

        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS signal_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          REAL NOT NULL,
                symbol      TEXT NOT NULL,
                signal_name TEXT NOT NULL,
                direction   TEXT NOT NULL,
                score       INTEGER NOT NULL,
                price       REAL,
                exchange    TEXT DEFAULT '',
                meta        TEXT,          -- JSON
                evaluated   INTEGER DEFAULT 0,
                exit_price  REAL,
                exit_ts     REAL,
                result      TEXT DEFAULT 'pending',  -- win / loss / pending
                pnl_pct     REAL
            )
        """)
        await self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sig_hist_eval
            ON signal_history(evaluated)
        """)
        await self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sig_hist_ts
            ON signal_history(ts)
        """)
        await self._conn.commit()
        logger.info("SignalDB ready at %s", self.db_path)

    # ── запись ─────────────────────────────────────────────

    async def record(
        self,
        signal_name: str,
        symbol: str,
        direction: str,
        score: float,
        price: float | None,
        exchange: str = "",
        meta: dict | None = None,
    ):
        """Сохранить сигнал в историю."""
        await self._conn.execute(
            """
            INSERT INTO signal_history
                (ts, symbol, signal_name, direction, score, price, exchange, meta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                symbol,
                signal_name,
                direction,
                int(round(score)),
                price,
                exchange,
                json.dumps(meta, default=str) if meta else None,
            ),
        )
        await self._conn.commit()

    # ── оценка win/loss ────────────────────────────────────

    async def get_pending(self, min_age_minutes: int = 30) -> list[dict]:
        """Сигналы, по которым ещё не оценили результат."""
        cutoff = time.time() - min_age_minutes * 60
        cursor = await self._conn.execute(
            """
            SELECT id, symbol, direction, price, ts
            FROM signal_history
            WHERE evaluated = 0 AND ts <= ?
            """,
            (cutoff,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def evaluate(
        self,
        signal_id: int,
        exit_price: float | None,
        result: str,
        pnl_pct: float | None,
    ):
        """Записать результат оценки."""
        await self._conn.execute(
            """
            UPDATE signal_history
            SET evaluated = 1, exit_price = ?, exit_ts = ?, result = ?, pnl_pct = ?
            WHERE id = ?
            """,
            (exit_price, time.time(), result, pnl_pct, signal_id),
        )
        await self._conn.commit()

    # ── статистика ─────────────────────────────────────────

    async def get_stats(
        self,
        signal_name: str | None = None,
        days: int = 7,
    ) -> dict:
        """Сводка win/loss за N дней."""
        cutoff = time.time() - days * 86400
        base = "FROM signal_history WHERE ts >= ?"
        params: list = [cutoff]

        if signal_name:
            base += " AND signal_name = ?"
            params.append(signal_name)

        cursor = await self._conn.execute(f"""
            SELECT
                COUNT(*)                                        AS total,
                SUM(CASE WHEN result = 'win'   THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN result = 'loss'  THEN 1 ELSE 0 END) AS losses,
                SUM(CASE WHEN result = 'pending' THEN 1 ELSE 0 END) AS pending,
                AVG(CASE WHEN result IN ('win','loss')
                    THEN pnl_pct ELSE NULL END)                 AS avg_pnl
            {base}
        """, params)
        row = await cursor.fetchone()
        if not row:
            return {"total": 0, "wins": 0, "losses": 0, "pending": 0, "avg_pnl": 0}

        d = dict(row)
        d["avg_pnl"] = round(d["avg_pnl"] or 0.0, 2)
        return d

    async def get_top_signals(self, days: int = 7, limit: int = 10) -> list[dict]:
        """Топ сигналов по win rate."""
        cutoff = time.time() - days * 86400
        cursor = await self._conn.execute(f"""
            SELECT
                signal_name,
                COUNT(*)                                        AS total,
                SUM(CASE WHEN result = 'win'   THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN result = 'loss'  THEN 1 ELSE 0 END) AS losses,
                ROUND(AVG(CASE WHEN result IN ('win','loss')
                    THEN pnl_pct ELSE NULL END), 2)            AS avg_pnl
            FROM signal_history
            WHERE ts >= ? AND evaluated = 1
            GROUP BY signal_name
            HAVING total >= 3
            ORDER BY wins * 1.0 / NULLIF(total, 0) DESC
            LIMIT ?
        """, (cutoff, limit))
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def close(self):
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self):
        if self._conn is None:
            await self.init()
        return self

    async def __aexit__(self, *exc):
        await self.close()
