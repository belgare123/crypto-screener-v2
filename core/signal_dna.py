"""
Signal DNA — отпечаток состояния рынка в момент сигнала.

Для каждого сигнала сохраняется вектор признаков:
  - Volume: Z-score объёма, avg_volume, spike_ratio
  - OI: открытый интерес (если доступен)
  - Delta: CVD, buy/sell ratio, taker flow
  - Walls: стены стакана bid/ask
  - Momentum: MACD, RSI, EMA alignment (1m/5m/15m)
  - Price: position relative to support/resistance

DNA хранится в SQLite для последующего поиска (#12 Pattern Similarity)
и кластеризации (#13 AI Clustering).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

from core import SignalResult

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("CS_DB_PATH", str(Path(__file__).resolve().parent.parent / "signals.db"))


@dataclass
class SignalDNA:
    id: int = 0
    timestamp: float = 0.0
    symbol: str = ""
    signal_name: str = ""
    score: float = 0.0
    direction: str = "neutral"
    price: float = 0.0

    # Volume
    volume_z: float = 0.0
    volume_spike_ratio: float = 0.0
    avg_volume_1h: float = 0.0

    # OI
    oi_value: float = 0.0
    oi_change_pct: float = 0.0

    # Delta / Flow
    cvd_value: float = 0.0
    buy_sell_ratio: float = 1.0
    taker_buy_ratio: float = 0.5

    # Orderbook
    bid_wall_size: float = 0.0
    ask_wall_size: float = 0.0
    spread_pct: float = 0.0
    depth_ratio: float = 1.0

    # Momentum
    rsi_1m: float = 50.0
    rsi_5m: float = 50.0
    ema9_1m: float = 0.0
    ema26_1m: float = 0.0
    macd_histogram: float = 0.0

    # Price context
    nearest_support_pct: float = 0.0
    nearest_resistance_pct: float = 0.0
    atr_1h: float = 0.0

    # Market context
    btc_change_5m: float = 0.0
    sector: str = ""
    market_breadth_pct: float = 0.0

    # Raw JSON payload for extensibility
    extra: str = "{}"

    def to_dict(self) -> dict:
        return asdict(self)

    def to_vector(self) -> list[float]:
        """Числовой вектор для поиска/кластеризации."""
        return [
            self.volume_z,
            self.volume_spike_ratio,
            self.oi_change_pct,
            self.buy_sell_ratio,
            self.taker_buy_ratio,
            self.bid_wall_size / 1_000_000,
            self.ask_wall_size / 1_000_000,
            self.spread_pct,
            self.depth_ratio,
            self.rsi_1m / 100,
            self.rsi_5m / 100,
            self.macd_histogram,
            self.nearest_support_pct,
            self.nearest_resistance_pct,
            self.atr_1h,
            self.btc_change_5m,
            self.market_breadth_pct / 100,
        ]


class DNAStore:
    """
    SQLite-хранилище Signal DNA.
    """

    def __init__(self, db_path: str | Path = DB_PATH):
        self._path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def _connect(self):
        if self._conn is None:
            with self._lock:
                if self._conn is None:
                    self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
                    self._conn.row_factory = sqlite3.Row
                    self._conn.execute("PRAGMA journal_mode=WAL")
                    self._init_db()

    def _init_db(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS signal_dna (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                symbol TEXT NOT NULL,
                signal_name TEXT NOT NULL,
                score REAL,
                direction TEXT,
                price REAL,

                volume_z REAL DEFAULT 0,
                volume_spike_ratio REAL DEFAULT 0,
                avg_volume_1h REAL DEFAULT 0,

                oi_value REAL DEFAULT 0,
                oi_change_pct REAL DEFAULT 0,

                cvd_value REAL DEFAULT 0,
                buy_sell_ratio REAL DEFAULT 1,
                taker_buy_ratio REAL DEFAULT 0.5,

                bid_wall_size REAL DEFAULT 0,
                ask_wall_size REAL DEFAULT 0,
                spread_pct REAL DEFAULT 0,
                depth_ratio REAL DEFAULT 1,

                rsi_1m REAL DEFAULT 50,
                rsi_5m REAL DEFAULT 50,
                ema9_1m REAL DEFAULT 0,
                ema26_1m REAL DEFAULT 0,
                macd_histogram REAL DEFAULT 0,

                nearest_support_pct REAL DEFAULT 0,
                nearest_resistance_pct REAL DEFAULT 0,
                atr_1h REAL DEFAULT 0,

                btc_change_5m REAL DEFAULT 0,
                sector TEXT DEFAULT '',
                market_breadth_pct REAL DEFAULT 0,

                extra TEXT DEFAULT '{}'
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_dna_timestamp ON signal_dna(timestamp)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_dna_symbol ON signal_dna(symbol)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_dna_signal ON signal_dna(signal_name)
        """)
        self._conn.commit()

    def save(self, dna: SignalDNA):
        self._connect()
        COLUMNS = [
            "timestamp", "symbol", "signal_name", "score", "direction", "price",
            "volume_z", "volume_spike_ratio", "avg_volume_1h",
            "oi_value", "oi_change_pct",
            "cvd_value", "buy_sell_ratio", "taker_buy_ratio",
            "bid_wall_size", "ask_wall_size", "spread_pct", "depth_ratio",
            "rsi_1m", "rsi_5m", "ema9_1m", "ema26_1m", "macd_histogram",
            "nearest_support_pct", "nearest_resistance_pct", "atr_1h",
            "btc_change_5m", "sector", "market_breadth_pct",
            "extra",
        ]
        placeholders = ", ".join("?" * len(COLUMNS))
        col_names = ", ".join(COLUMNS)
        values = [getattr(dna, c) for c in COLUMNS]
        self._conn.execute(
            f"INSERT INTO signal_dna ({col_names}) VALUES ({placeholders})",
            values,
        )
        self._conn.commit()

    def get_recent(self, limit: int = 2000) -> list[SignalDNA]:
        """Получить последние DNA записи."""
        self._connect()
        rows = self._conn.execute(
            "SELECT * FROM signal_dna ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [self._row_to_dna(r) for r in rows]

    def search_similar(self, vector: list[float], limit: int = 10) -> list[SignalDNA]:
        """
        Поиск похожих DNA по евклидову расстоянию.
        Тяжёлый full-scan — на больших данных нужен индекс (FAISS/etc).
        """
        self._connect()
        rows = self._conn.execute(
            "SELECT * FROM signal_dna ORDER BY id DESC LIMIT 1000"
        ).fetchall()

        if not rows:
            return []

        scored = []
        for r in rows:
            dna = self._row_to_dna(r)
            dv = dna.to_vector()
            # Евклидово расстояние (нормализовано по длине)
            if len(dv) != len(vector):
                continue
            dist = sum((a - b) ** 2 for a, b in zip(dv, vector)) ** 0.5
            scored.append((dist, dna))

        scored.sort(key=lambda x: x[0])
        return [d for _, d in scored[:limit]]

    def find_by_time_range(self, start: float, end: float) -> list[SignalDNA]:
        self._connect()
        rows = self._conn.execute(
            "SELECT * FROM signal_dna WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            (start, end),
        ).fetchall()
        return [self._row_to_dna(r) for r in rows]

    def get_stats(self) -> dict[str, Any]:
        self._connect()
        total = self._conn.execute("SELECT COUNT(*) FROM signal_dna").fetchone()[0]
        per_signal = self._conn.execute(
            "SELECT signal_name, COUNT(*) FROM signal_dna GROUP BY signal_name ORDER BY COUNT(*) DESC"
        ).fetchall()
        return {
            "total_dna": total,
            "per_signal": dict(per_signal),
        }

    @staticmethod
    def _row_to_dna(row: sqlite3.Row) -> SignalDNA:
        return SignalDNA(**dict(row))


class DNACollector:
    """
    Собирает Signal DNA из текущего состояния рынка для каждого сигнала.
    """

    def __init__(
        self,
        ticker_getter: Callable,
        candle_getter: Callable,
        ob_getter: Callable,
        breadth_getter: Callable,
        sector_getter: Callable,
        store: DNAStore | None = None,
    ):
        self._ticker_getter = ticker_getter
        self._candle_getter = candle_getter
        self._ob_getter = ob_getter
        self._breadth_getter = breadth_getter
        self._sector_getter = sector_getter
        self._store = store or DNAStore()

    async def collect(self, signal: SignalResult) -> SignalDNA:
        """Собрать DNA для конкретного сигнала."""
        sym = signal.symbol
        short_sym = sym.split("/")[0]
        tickers = await self._ticker_getter()
        ticker = tickers.get(sym, {})

        candles_1m = self._candle_getter(sym, "1", 30)
        candles_5m = self._candle_getter(sym, "5", 20)
        ob = self._ob_getter(sym)

        closes_1m = [float(c.get("close", 0)) for c in candles_1m if c.get("close")]
        closes_5m = [float(c.get("close", 0)) for c in candles_5m if c.get("close")]

        dna = SignalDNA(
            timestamp=time.time(),
            symbol=sym,
            signal_name=signal.signal_name,
            score=signal.score,
            direction=signal.direction,
            price=float(ticker.get("lastPrice", ticker.get("closePrice", 0))),
        )

        # Volume
        dna.volume_z = float(ticker.get("volume_z", 0))
        dna.volume_spike_ratio = float(ticker.get("volume_spike_ratio", 0))
        dna.avg_volume_1h = float(ticker.get("volume", ticker.get("volume_24h", 0)))

        # OI (если есть)
        dna.oi_value = float(ticker.get("open_interest", 0))
        dna.oi_change_pct = float(ticker.get("oi_change_pct", 0))

        # Delta / CVD
        dna.cvd_value = float(ticker.get("cvd", ticker.get("cumulative_delta", 0)))
        dna.buy_sell_ratio = float(ticker.get("buy_sell_ratio", 1.0))
        dna.taker_buy_ratio = float(ticker.get("taker_buy_ratio", 0.5))

        # Orderbook
        if ob:
            bids = ob.get("bids", [])
            asks = ob.get("asks", [])
            if bids:
                dna.bid_wall_size = sum(float(b[1]) * float(b[0]) for b in bids[:3])
            if asks:
                dna.ask_wall_size = sum(float(a[1]) * float(a[0]) for a in asks[:3])
        dna.spread_pct = float(ticker.get("spread_pct", 0))
        dna.depth_ratio = float(ticker.get("depth_ratio", 1.0))

        # Momentum / RSI
        if len(closes_1m) >= 14:
            gains = [max(closes_1m[i] - closes_1m[i - 1], 0) for i in range(1, 14)]
            losses = [max(closes_1m[i - 1] - closes_1m[i], 0) for i in range(1, 14)]
            avg_gain = sum(gains) / 14
            avg_loss = sum(losses) / 14
            dna.rsi_1m = 100 - (100 / (1 + avg_gain / max(avg_loss, 0.0001))) if avg_loss > 0 else 100

        if len(closes_5m) >= 14:
            gains = [max(closes_5m[i] - closes_5m[i - 1], 0) for i in range(1, 14)]
            losses = [max(closes_5m[i - 1] - closes_5m[i], 0) for i in range(1, 14)]
            avg_gain = sum(gains) / 14
            avg_loss = sum(losses) / 14
            dna.rsi_5m = 100 - (100 / (1 + avg_gain / max(avg_loss, 0.0001))) if avg_loss > 0 else 100

        if len(closes_1m) >= 26:
            dna.ema9_1m = closes_1m[-1]  # упрощённо
            dna.ema26_1m = closes_1m[-1]

        # MACD-like histogram
        if len(closes_5m) >= 26:
            ema12 = sum(closes_5m[-12:]) / 12
            ema26 = sum(closes_5m[-26:]) / 26
            dna.macd_histogram = ema12 - ema26

        # Price context
        price = dna.price
        if price > 0:
            dna.nearest_support_pct = float(ticker.get("nearest_support_pct", 0))
            dna.nearest_resistance_pct = float(ticker.get("nearest_resistance_pct", 0))

        # ATR 1h
        if len(closes_5m) >= 12:
            highs = [float(c.get("high", price)) for c in candles_5m[-12:]]
            lows = [float(c.get("low", price)) for c in candles_5m[-12:]]
            true_ranges = []
            for i in range(1, len(highs)):
                tr = max(highs[i] - lows[i], abs(highs[i] - closes_5m[i - 1]), abs(lows[i] - closes_5m[i - 1]))
                true_ranges.append(tr)
            dna.atr_1h = sum(true_ranges) / max(len(true_ranges), 1)

        # Market context
        btc_ticker = tickers.get("BTC/USDT:USDT", {})
        btc_change = float(btc_ticker.get("price24hPcnt", btc_ticker.get("change_24h", 0)))
        dna.btc_change_5m = btc_change * 100

        dna.sector = await self._sector_getter(sym)
        dna.market_breadth_pct = await self._breadth_getter()

        # Сохраняем
        self._store.save(dna)
        logger.info("[dna] saved %s %s score=%.0f", sym, signal.signal_name, signal.score)
        return dna


_dna_collector: DNACollector | None = None
_dna_store: DNAStore | None = None


def get_dna_store() -> DNAStore:
    global _dna_store
    if _dna_store is None:
        _dna_store = DNAStore()
    return _dna_store


def get_dna_collector(
    ticker_getter=None,
    candle_getter=None,
    ob_getter=None,
    breadth_getter=None,
    sector_getter=None,
) -> DNACollector:
    global _dna_collector
    if _dna_collector is None and all(x is not None for x in [ticker_getter, candle_getter, ob_getter, breadth_getter, sector_getter]):
        _dna_collector = DNACollector(ticker_getter, candle_getter, ob_getter, breadth_getter, sector_getter)
    return _dna_collector
