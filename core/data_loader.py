"""
data_loader — универсальный загрузчик Parquet-данных для бэктеста.

Имена файлов: data/raw/{SYMBOL}_{INTERVAL}.parquet
  - BTCUSDT_1h.parquet   (klines)
  - BTCUSDT_trades.parquet (trades)
  - BTCUSDT_orderbook.parquet (orderbook snapshots)

Символ нормализуется: BTC/USDT:USDT → BTCUSDT
Интервалы: 1m, 5m, 15m, 30m, 1h, 4h, 1d
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

logger = logging.getLogger(__name__)

DATA_DIR = Path("data/raw")


def normalize_symbol(symbol: str) -> str:
    """BTC/USDT:USDT → BTCUSDT"""
    return symbol.replace("/", "").replace(":USDT", "")


# ---------------------------------------------------------------------------
# Klines
# ---------------------------------------------------------------------------


def load_klines(
    symbol: str,
    interval: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    as_timestamp_ms: bool = True,
) -> pd.DataFrame:
    """
    Загрузить свечи из Parquet.

    Returns
    -------
    pd.DataFrame
        columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
        sorted by timestamp ascending.
        timestamp — int64 ms (if as_timestamp_ms=True) или datetime64.
    """
    safe = normalize_symbol(symbol)
    path = DATA_DIR / f"{safe}_{interval}.parquet"

    if not path.exists():
        raise FileNotFoundError(f"Parquet not found: {path}")

    df: pd.DataFrame = pd.read_parquet(path)  # type: ignore[assignment]

    if "timestamp" not in df.columns:
        raise KeyError(f"Parquet {path} missing 'timestamp' column; has {list(df.columns)}")

    # Всегда numeric для pipeline
    df["timestamp"] = pd.to_numeric(df["timestamp"])

    # Фильтр по дате (поддержка строк '2026-01-01' и чисел ms)
    if start_date is not None:
        start_ms = _to_ms(start_date)
        df = df[df["timestamp"] >= start_ms]
    if end_date is not None:
        end_ms = _to_ms(end_date)
        df = df[df["timestamp"] <= end_ms]

    if not as_timestamp_ms:
        df = df.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")

    return df.sort_values("timestamp").reset_index(drop=True)


def _to_ms(val: str | int | float) -> int:
    """Привести дату к ms."""
    if isinstance(val, (int, float)):
        return int(val)
    # Строка формата YYYY-MM-DD или ISO
    dt = pd.to_datetime(val)
    return int(dt.timestamp() * 1000)


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------


def load_trades(symbol: str, limit: int = 10000) -> pd.DataFrame:
    """Загрузить сделки (последние ``limit``)."""
    safe = normalize_symbol(symbol)
    path = DATA_DIR / f"{safe}_trades.parquet"
    if not path.exists():
        logger.warning("[data_loader] trades not found: %s", path)
        return pd.DataFrame()
    df: pd.DataFrame = pd.read_parquet(path)  # type: ignore[assignment]
    if len(df) > limit:
        df = df.tail(limit)
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Orderbook snapshot
# ---------------------------------------------------------------------------


def load_orderbook_snapshot(symbol: str) -> Dict[str, Any]:
    """Последний L2 snapshot."""
    safe = normalize_symbol(symbol)
    path = DATA_DIR / f"{safe}_orderbook.parquet"
    if not path.exists():
        logger.warning("[data_loader] orderbook not found: %s", path)
        return {}
    df: pd.DataFrame = pd.read_parquet(path)  # type: ignore[assignment]
    if df.empty:
        return {}
    return dict(df.iloc[-1])
