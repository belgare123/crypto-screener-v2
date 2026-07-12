"""
Market Replay (#19) — запись и воспроизведение исторического состояния рынка.

Каждые N минут сохраняет снэпшот: цена, объём, OI, стакан, CVD, активные сигналы.
Позволяет запросить состояние в любой момент времени.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core import SignalResult

logger = logging.getLogger(__name__)

SNAPSHOT_INTERVAL = 300       # каждые 5 минут
MAX_SNAPSHOTS_PER_SYMBOL = 5000


@dataclass
class MarketSnapshot:
    id: int = 0
    timestamp: float = 0.0
    symbol: str = ""
    price: float = 0.0
    volume_24h: float = 0.0
    open_interest: float = 0.0
    funding_rate: float = 0.0
    bid_depth: float = 0.0        # сумма объёмов бидов (топ-10)
    ask_depth: float = 0.0        # сумма объёмов асков (топ-10)
    spread: float = 0.0
    cvd: float = 0.0              # Cumulative Volume Delta
    active_signals: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


@dataclass
class ReplayWindow:
    """Временное окно с историческими данными."""
    symbol: str = ""
    start_ts: float = 0.0
    end_ts: float = 0.0
    snapshots: list[MarketSnapshot] = field(default_factory=list)
    price_open: float = 0.0
    price_close: float = 0.0
    price_high: float = 0.0
    price_low: float = 0.0
    price_change: float = 0.0
    signals_fired: list[dict] = field(default_factory=list)
    total_snapshots: int = 0
    description: str = ""


class MarketReplayEngine:
    """
    Запись и воспроизведение исторических снэпшотов.
    """

    def __init__(self, db_path: str | Path | None = None):
        self._db_path = Path(db_path) if db_path else Path("market_replay.db")
        self._conn: sqlite3.Connection | None = None
        self._conn_lock = threading.Lock()
        self._active_signals: dict[str, list[dict]] = {}  # symbol → [signal dicts]
        self._signal_listener: Callable | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            with self._conn_lock:
                if self._conn is None:
                    self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
                    self._conn.execute("PRAGMA journal_mode=WAL")
                    self._conn.execute("PRAGMA synchronous=OFF")
                    self._init_db()
        return self._conn

    def _init_db(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS market_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                symbol TEXT NOT NULL,
                price REAL DEFAULT 0,
                volume_24h REAL DEFAULT 0,
                open_interest REAL DEFAULT 0,
                funding_rate REAL DEFAULT 0,
                bid_depth REAL DEFAULT 0,
                ask_depth REAL DEFAULT 0,
                spread REAL DEFAULT 0,
                cvd REAL DEFAULT 0,
                active_signals TEXT DEFAULT '[]',
                meta TEXT DEFAULT '{}'
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_snapshots_symbol_ts
            ON market_snapshots(symbol, timestamp DESC)
        """)
        self._conn.commit()

    def record_signal(self, symbol: str, signal_name: str, score: float, direction: str, meta: dict | None = None):
        """Зафиксировать активный сигнал для символа."""
        if symbol not in self._active_signals:
            self._active_signals[symbol] = []
        entry = {
            "signal_name": signal_name,
            "score": round(score, 0),
            "direction": direction or "neutral",
            "ts": time.time(),
        }
        self._active_signals[symbol].append(entry)
        # Keep last 20 per symbol
        if len(self._active_signals[symbol]) > 20:
            self._active_signals[symbol] = self._active_signals[symbol][-20:]

    def clear_old_signals(self, symbol: str, max_age: float = 3600):
        """Очистить сигналы старше max_age секунд для символа."""
        now = time.time()
        if symbol in self._active_signals:
            self._active_signals[symbol] = [
                s for s in self._active_signals[symbol]
                if now - s["ts"] < max_age
            ]

    def save_snapshot(self, symbol: str, ticker: dict | None = None,
                      orderbook: dict | None = None, cvd: float | None = None) -> int | None:
        """Сохранить снэпшот текущего состояния."""
        conn = self._get_conn()
        now = time.time()
        price = float(ticker.get("last_price", 0)) if ticker else 0
        volume = float(ticker.get("volume_24h", 0)) if ticker else 0
        oi = float(ticker.get("open_interest", 0)) if ticker else 0
        fr = float(ticker.get("funding_rate", 0)) if ticker else 0

        bid = float(ticker.get("bid_price", 0)) if ticker else 0
        ask = float(ticker.get("ask_price", 0)) if ticker else 0
        spread = ask - bid if ask > 0 and bid > 0 else 0

        # Orderbook depth
        bid_depth = 0.0
        ask_depth = 0.0
        if orderbook:
            bids = orderbook.get("bids", [])
            asks = orderbook.get("asks", [])
            for b in bids[:10]:
                bid_depth += float(b[1]) if isinstance(b, (list, tuple)) else 0
            for a in asks[:10]:
                ask_depth += float(a[1]) if isinstance(a, (list, tuple)) else 0

        # Очищаем старые сигналы и собираем активные
        self.clear_old_signals(symbol)
        signals = self._active_signals.get(symbol, [])[:10]

        conn.execute(
            """INSERT INTO market_snapshots
               (timestamp, symbol, price, volume_24h, open_interest, funding_rate,
                bid_depth, ask_depth, spread, cvd, active_signals)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (now, symbol, price, volume, oi, fr,
             bid_depth, ask_depth, spread, cvd or 0,
             json.dumps(signals))
        )

        # Trim excess
        conn.execute("""
            DELETE FROM market_snapshots WHERE id IN (
                SELECT id FROM market_snapshots WHERE symbol = ?
                ORDER BY id DESC LIMIT -1 OFFSET ?
            )
        """, (symbol, MAX_SNAPSHOTS_PER_SYMBOL))
        conn.commit()

        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def get_replay(self, symbol: str, start_ts: float, end_ts: float | None = None,
                   limit: int = 100) -> ReplayWindow:
        """Получить окно исторических данных."""
        conn = self._get_conn()
        end = end_ts or time.time()

        rows = conn.execute(
            """SELECT * FROM market_snapshots
               WHERE symbol = ? AND timestamp >= ? AND timestamp <= ?
               ORDER BY timestamp ASC LIMIT ?""",
            (symbol, start_ts, end, limit)
        ).fetchall()

        snapshots = []
        for r in rows:
            snapshots.append(MarketSnapshot(
                id=r[0], timestamp=r[1], symbol=r[2],
                price=r[3], volume_24h=r[4], open_interest=r[5],
                funding_rate=r[6], bid_depth=r[7], ask_depth=r[8],
                spread=r[9], cvd=r[10],
                active_signals=json.loads(r[11] or "[]"),
                meta=json.loads(r[12] or "{}"),
            ))

        if not snapshots:
            return ReplayWindow(symbol=symbol, start_ts=start_ts, end_ts=end, description="No data")

        prices = [s.price for s in snapshots if s.price > 0]

        # Собираем уникальные сигналы за период
        all_signals = []
        seen = set()
        for s in snapshots:
            for sig in s.active_signals:
                key = (sig["signal_name"], sig["score"])
                if key not in seen:
                    seen.add(key)
                    all_signals.append(sig)

        price_change = ((snapshots[-1].price - snapshots[0].price) / max(snapshots[0].price, 0.001) * 100) if snapshots[0].price > 0 else 0

        desc = f"Replay {symbol}: {len(snapshots)} snapshots, {prices[0]} → {prices[-1]} ({price_change:.2f}%)" if prices else f"Replay {symbol}: {len(snapshots)} snapshots"
        window = ReplayWindow(
            symbol=symbol,
            start_ts=snapshots[0].timestamp,
            end_ts=snapshots[-1].timestamp,
            snapshots=snapshots,
            price_open=snapshots[0].price,
            price_close=snapshots[-1].price,
            price_high=max(prices) if prices else 0,
            price_low=min(prices) if prices else 0,
            price_change=price_change,
            signals_fired=all_signals,
            total_snapshots=len(snapshots),
            description=desc,
        )
        return window

    def format_replay_for_telegram(self, window: ReplayWindow) -> str:
        """Отформатировать replay для сообщения."""
        if not window.snapshots:
            return f"📼 Replay {window.symbol}: нет данных"

        lines = [
            f"📼 *Replay {window.symbol}*",
            f"🕐 {datetime.fromtimestamp(window.start_ts, tz=timezone.utc).strftime('%H:%M:%S')} → "
            f"{datetime.fromtimestamp(window.end_ts, tz=timezone.utc).strftime('%H:%M:%S')}",
            f"💵 {window.price_open:.2f} → {window.price_close:.2f} "
            f"({window.price_change:+.2f}%)  H={window.price_high:.2f} L={window.price_low:.2f}",
            f"📊 Снэпшотов: {window.total_snapshots}",
        ]

        if window.signals_fired:
            lines.append(f"\n🔔 *Сигналы ({len(window.signals_fired)}):*")
            for sig in window.signals_fired[:8]:
                dir_icon = "🟢" if sig.get("direction") == "buy" else ("🔴" if sig.get("direction") == "sell" else "⚪")
                lines.append(f"  {dir_icon} {sig['signal_name']}: {sig['score']:.0f}/100")

        # Последний снэпшот
        last = window.snapshots[-1]
        lines.append(f"\n📌 *Последний снэпшот:*")
        lines.append(f"  💰 Объём 24ч: ${last.volume_24h:,.0f}")
        lines.append(f"  📊 OI: ${last.open_interest:,.0f}")
        if last.bid_depth > 0 or last.ask_depth > 0:
            lines.append(f"  📖 Стакан: Bid ${last.bid_depth:,.0f} / Ask ${last.ask_depth:,.0f}")
        if last.spread > 0:
            lines.append(f"  ↔ Спред: ${last.spread:.4f}")
        if last.cvd != 0:
            lines.append(f"  📈 CVD: {last.cvd:+.2f}")

        return "\n".join(lines)


_market_replay_engine: MarketReplayEngine | None = None


def get_market_replay_engine(db_path: str | None = None) -> MarketReplayEngine:
    global _market_replay_engine
    if _market_replay_engine is None:
        _market_replay_engine = MarketReplayEngine(db_path)
    return _market_replay_engine
