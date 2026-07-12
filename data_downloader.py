#!/usr/bin/env python3
"""
DataDownloader — загрузка исторических данных с Bybit через REST API v5.

Поддерживаемые типы данных:
  - klines (OHLCV): 1m, 5m, 15m, 30m, 1h, 4h, 1d
  - public trades (trades)
  - orderbook snapshots (L2)

Формат хранения: Parquet (сжатие zstd) в data/raw/

Инкрементальная загрузка: скрипт проверяет, какие данные уже есть в Parquet,
и скачивает только недостающий диапазон.

Использование:
  # Скачать свечи за 90 дней
  python data_downloader.py --symbol BTC/USDT:USDT --type klines --interval 1h --days 90

  # Скачать несколько таймфреймов за период
  python data_downloader.py --symbol BTC/USDT:USDT --type klines --interval 15m --start 2024-01-01 --end 2024-06-01

  # Скачать трейды
  python data_downloader.py --symbol BTC/USDT:USDT --type trades --days 7

  # Скачать стакан (один снэпшот)
  python data_downloader.py --symbol BTC/USDT:USDT --type orderbook --depth 50

  # Скачать всё для нескольких пар
  python data_downloader.py --symbols BTC/USDT:USDT,ETH/USDT:USDT --type klines --interval 1h --days 30
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

# Константы
BASE_URL = "https://api.bybit.com"
BASE_URL_TESTNET = "https://api-testnet.bybit.com"

DATA_DIR = Path(__file__).parent / "data" / "raw"

# Bybit interval → читаемое название
INTERVAL_MAP: dict[str, str] = {
    "1": "1m", "3": "3m", "5": "5m", "15": "15m",
    "30": "30m", "60": "1h", "120": "2h", "240": "4h",
    "360": "6h", "720": "12h", "D": "1d", "W": "1w", "M": "1M",
}
INTERVAL_REVERSE = {v: k for k, v in INTERVAL_MAP.items()}

BYBIT_INTERVALS = {"1", "3", "5", "15", "30", "60", "120", "240", "360", "720", "D", "W", "M"}

# Максимальное количество свечей за один запрос
BYBIT_MAX_LIMIT = 200
# Пауза между запросами (сек) — избежать rate limit
REQUEST_DELAY = 1.0
# Максимальное количество параллельных запросов
MAX_CONCURRENT = 5


# ---------------------------------------------------------------------------
# Bybit REST API
# ---------------------------------------------------------------------------

def normalize_symbol(symbol: str) -> str:
    """BTC/USDT:USDT → BTCUSDT"""
    return symbol.replace("/", "").replace(":USDT", "")


def denormalize_symbol(raw: str) -> str:
    """BTCUSDT → BTC/USDT:USDT"""
    if raw.endswith("USDT"):
        base = raw[:-4]
        return f"{base}/USDT:USDT"
    return raw


async def fetch_klines(
    session: aiohttp.ClientSession,
    symbol: str,
    interval: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    limit: int = BYBIT_MAX_LIMIT,
    category: str = "linear",
) -> list[dict[str, Any]]:
    """
    Загрузить свечи через Bybit v5 GET /v5/market/kline.

    interval: 1, 3, 5, 15, 30, 60, 120, 240, 360, 720, D, W, M
    start_ms / end_ms: timestamp в миллисекундах (опционально)
    """
    raw_symbol = normalize_symbol(symbol)
    params: dict[str, Any] = {
        "category": category,
        "symbol": raw_symbol,
        "interval": interval,
        "limit": limit,
    }
    if start_ms is not None:
        params["start"] = start_ms
    if end_ms is not None:
        params["end"] = end_ms

    url = f"{BASE_URL}/v5/market/kline"
    async with session.get(url, params=params) as resp:
        if resp.status != 200:
            logger.error("[klines] HTTP %d for %s %s: %s", resp.status, symbol, interval, await resp.text())
            return []
        data = await resp.json()
        if data.get("retCode") != 0:
            logger.error("[klines] API error %s: %s", symbol, data.get("retMsg", ""))
            return []
        result = data.get("result", {})
        raw_list = result.get("list", [])
        # Bybit возвращает свечи от новых к старым (list[0] = самая новая)
        raw_list.reverse()  # → от старых к новым

        parsed = []
        for entry in raw_list:
            # [timestamp, open, high, low, close, volume, turnover]
            ts_ms = int(entry[0])
            parsed.append({
                "timestamp": ts_ms,
                "open": float(entry[1]),
                "high": float(entry[2]),
                "low": float(entry[3]),
                "close": float(entry[4]),
                "volume": float(entry[5]),
                "turnover": float(entry[6]) if len(entry) > 6 else 0.0,
            })
        logger.debug("[klines] %s %s: got %d candles", symbol, interval, len(parsed))
        return parsed


async def fetch_trades(
    session: aiohttp.ClientSession,
    symbol: str,
    limit: int = 1000,
    category: str = "linear",
) -> list[dict[str, Any]]:
    """
    Загрузить публичные сделки через Bybit v5 GET /v5/market/public-trades.
    Максимум 1000 сделок, самые свежие.
    """
    raw_symbol = normalize_symbol(symbol)
    params = {"category": category, "symbol": raw_symbol, "limit": min(limit, 1000)}

    url = f"{BASE_URL}/v5/market/recent-trade"
    async with session.get(url, params=params) as resp:
        if resp.status != 200:
            logger.error("[trades] HTTP %d for %s", resp.status, symbol)
            return []
        data = await resp.json()
        if data.get("retCode") != 0:
            return []
        result = data.get("result", {})
        raw_list = result.get("list", [])

        parsed = []
        for entry in raw_list:
            parsed.append({
                "timestamp": int(entry.get("time", 0)),
                "side": entry.get("side", "").lower(),
                "price": float(entry.get("price", 0)),
                "size": float(entry.get("size", 0)),
                "id": entry.get("execId", ""),
            })
        return parsed


async def fetch_orderbook(
    session: aiohttp.ClientSession,
    symbol: str,
    depth: int = 50,
    category: str = "linear",
) -> dict[str, Any] | None:
    """
    Загрузить снэпшот стакана через Bybit v5 GET /v5/market/orderbook.
    depth: 1, 50, 200, 500
    """
    raw_symbol = normalize_symbol(symbol)
    params = {"category": category, "symbol": raw_symbol, "limit": depth}

    url = f"{BASE_URL}/v5/market/orderbook"
    async with session.get(url, params=params) as resp:
        if resp.status != 200:
            logger.error("[orderbook] HTTP %d for %s", resp.status, symbol)
            return None
        data = await resp.json()
        if data.get("retCode") != 0:
            return None
        result = data.get("result", {})
        bids = result.get("b", [])
        asks = result.get("a", [])
        ts = int(result.get("ts", 0))

        return {
            "timestamp": ts,
            "symbol": symbol,
            "bid_prices": [float(b[0]) for b in bids],
            "bid_sizes": [float(b[1]) for b in bids],
            "ask_prices": [float(a[0]) for a in asks],
            "ask_sizes": [float(a[1]) for a in asks],
            "bid_depth": sum(float(b[1]) for b in bids),
            "ask_depth": sum(float(a[1]) for a in asks),
            "spread": (float(asks[0][0]) - float(bids[0][0])) if bids and asks else 0.0,
        }


# ---------------------------------------------------------------------------
# Parquet I/O
# ---------------------------------------------------------------------------

def klines_filename(symbol: str, interval: str) -> str:
    """data/raw/BTCUSDT_1h.parquet — использует человекочитаемый интервал"""
    raw = normalize_symbol(symbol)
    display = INTERVAL_MAP.get(interval, interval)
    return f"{raw}_{display}.parquet"


def trades_filename(symbol: str) -> str:
    raw = normalize_symbol(symbol)
    return f"{raw}_trades.parquet"


def orderbook_filename(symbol: str) -> str:
    raw = normalize_symbol(symbol)
    return f"{raw}_orderbook.parquet"


def load_existing_klines(path: Path) -> pd.DataFrame:
    """Загрузить существующий Parquet, вернуть DataFrame (пустой, если нет файла)."""
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pq.read_table(path).to_pandas()
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_numeric(df["timestamp"])
        return df
    except Exception as e:
        logger.warning("[load] error reading %s: %s — starting fresh", path.name, e)
        return pd.DataFrame()


def save_parquet(df: pd.DataFrame, path: Path, dedup_col: str | None = "timestamp"):
    """Save DataFrame to Parquet (zstd) with optional deduplication."""
    if df.empty:
        logger.warning("[save] empty DataFrame, skipping %s", path.name)
        return
    if dedup_col:
        df = df.sort_values(dedup_col).drop_duplicates(subset=[dedup_col], keep="last")
    else:
        df = df.sort_values(df.columns[0]) if len(df.columns) > 0 else df
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, path, compression="zstd")
    logger.info("[save] saved %d rows → %s", len(df), path) 


# ---------------------------------------------------------------------------
# Инкрементальная загрузка свечей
# ---------------------------------------------------------------------------

def interval_to_ms(interval: str) -> int:
    """Convert Bybit interval string to milliseconds."""
    mapping = {
        "1": 60_000, "3": 180_000, "5": 300_000, "15": 900_000,
        "30": 1_800_000, "60": 3_600_000, "120": 7_200_000, "240": 14_400_000,
        "360": 21_600_000, "720": 43_200_000, "D": 86_400_000, "W": 604_800_000,
        "M": 2_592_000_000,
    }
    return mapping.get(interval, 60_000)


async def download_klines_incremental(
    session: aiohttp.ClientSession,
    symbol: str,
    interval_raw: str,
    start_dt: datetime,
    end_dt: datetime,
) -> pd.DataFrame:
    """
    Скачать свечи в диапазоне [start_dt, end_dt].
    Если Parquet уже существует, восстанавливает прогресс и докачивает только
    отсутствующий диапазон.

    Returns: полный DataFrame (старые + новые данные).
    """
    path = DATA_DIR / klines_filename(symbol, interval_raw)

    # Загружаем существующие данные
    existing = load_existing_klines(path)

    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    if not existing.empty:
        max_ts = int(existing["timestamp"].max())
        if max_ts >= end_ms:
            logger.info("[incremental] %s %s: already up-to-date (%d rows)", symbol, interval_raw, len(existing))
            return existing
        if max_ts > start_ms:
            # Resume from the interval boundary after the last candle
            interval_ms = interval_to_ms(interval_raw)
            start_ms = (max_ts // interval_ms + 1) * interval_ms
            logger.info("[incremental] %s %s: resuming from %s (%d existing rows)",
                        symbol, interval_raw,
                        datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc),
                        len(existing))

    # Forward pagination using explicit time windows
    # Bybit returns newest-first with max BYBIT_MAX_LIMIT candles.
    # We paginate by splitting the range into windows of BYBIT_MAX_LIMIT*interval duration.
    all_new: list[dict] = []
    interval_ms = interval_to_ms(interval_raw)
    window = interval_ms * BYBIT_MAX_LIMIT  # ms covered by one full batch
    window_start = start_ms

    while window_start < end_ms:
        window_end = min(window_start + window, end_ms)
        candles = await fetch_klines(
            session, symbol, interval_raw,
            start_ms=window_start, end_ms=window_end, limit=BYBIT_MAX_LIMIT,
        )
        if not candles:
            window_start = window_end
            continue

        # Bybit returns newest-first → reverse to oldest-first
        candles.reverse()
        all_new.extend(candles)
        window_start = window_end

        await asyncio.sleep(REQUEST_DELAY)

    logger.info("[download] %s %s: fetched %d new candles in %d batches",
                symbol, interval_raw, len(all_new),
                (end_ms - start_ms) // window + 1)

    # Объединяем старые и новые
    if all_new:
        df_new = pd.DataFrame(all_new)
        if not existing.empty:
            combined = pd.concat([existing, df_new], ignore_index=True)
        else:
            combined = df_new
        combined = combined.sort_values("timestamp").drop_duplicates(subset=["timestamp"], keep="last")
    else:
        combined = existing

    return combined


async def download_and_save_klines(
    session: aiohttp.ClientSession,
    symbol: str,
    interval: str,
    start_dt: datetime,
    end_dt: datetime,
):
    """Скачать + сохранить в Parquet."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    interval_raw = INTERVAL_REVERSE.get(interval, interval)
    if interval_raw not in BYBIT_INTERVALS:
        logger.error("[klines] unsupported interval: %s (use: %s)", interval, ", ".join(sorted(INTERVAL_MAP.keys(), key=lambda x: (isinstance(x, str) and not x.isdigit(), x))))
        return

    path = DATA_DIR / klines_filename(symbol, interval_raw)
    df = await download_klines_incremental(session, symbol, interval_raw, start_dt, end_dt)
    path = DATA_DIR / klines_filename(symbol, interval_raw)
    save_parquet(df, path, dedup_col="timestamp")
    return path, len(df)


async def download_and_save_trades(
    session: aiohttp.ClientSession,
    symbol: str,
):
    """Скачать и сохранить последние 1000 сделок."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    trades = await fetch_trades(session, symbol)
    if not trades:
        logger.warning("[trades] no data for %s", symbol)
        return

    df = pd.DataFrame(trades)
    path = DATA_DIR / trades_filename(symbol)

    # Инкрементально: мерджим с существующими, убираем дубликаты по id
    existing = load_existing_klines(path)  # переиспользуем, формат тот же
    if not existing.empty and "id" in existing.columns:
        existing_ids = set(existing["id"].astype(str))
        df = df[~df["id"].astype(str).isin(existing_ids)]

    if not df.empty:
        combined = pd.concat([existing, df], ignore_index=True) if not existing.empty else df
        save_parquet(combined, path, dedup_col=None)  # trades deduped by id above
        logger.info("[trades] saved %d new trades for %s", len(df), symbol)
    else:
        logger.info("[trades] no new trades for %s", symbol)


async def download_and_save_orderbook(
    session: aiohttp.ClientSession,
    symbol: str,
    depth: int = 50,
):
    """Скачать один снэпшот стакана и сохранить."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    snap = await fetch_orderbook(session, symbol, depth=depth)
    if not snap:
        logger.warning("[orderbook] no data for %s", symbol)
        return

    df = pd.DataFrame([snap])
    path = DATA_DIR / orderbook_filename(symbol)

    existing = load_existing_klines(path)
    if not existing.empty:
        combined = pd.concat([existing, df], ignore_index=True)
    else:
        combined = df
    save_parquet(combined, path, dedup_col=None)  # no dedup for orderbook
    logger.info("[orderbook] saved snapshot for %s (depth=%d)", symbol, depth)


# ---------------------------------------------------------------------------
# Пакетная загрузка нескольких символов/таймфреймов
# ---------------------------------------------------------------------------

semaphore = asyncio.Semaphore(MAX_CONCURRENT)


async def _limited_download_klines(session, symbol, interval, start, end):
    async with semaphore:
        return await download_and_save_klines(session, symbol, interval, start, end)


async def batch_download_klines(
    symbols: list[str],
    intervals: list[str],
    start_dt: datetime,
    end_dt: datetime,
):
    """Скачать свечи для всех symbols × intervals параллельно (с семафором)."""
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=MAX_CONCURRENT + 2)
    ) as session:
        tasks = []
        for symbol in symbols:
            for interval in intervals:
                tasks.append(_limited_download_klines(session, symbol, interval, start_dt, end_dt))
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results):
            symbol = symbols[i // len(intervals)]
            interval = intervals[i % len(intervals)]
            if isinstance(result, Exception):
                logger.error("[batch] %s %s failed: %s", symbol, interval, result)
            elif result:
                path, count = result
                logger.info("[batch] %s %s → %s (%d rows)", symbol, interval, path.name, count)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="DataDownloader — загрузка исторических данных с Bybit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Какие данные скачивать
    parser.add_argument("--type", choices=["klines", "trades", "orderbook", "all"],
                        default="klines", help="Тип данных (по умолч. klines)")

    # Символы
    parser.add_argument("--symbol", type=str, default=None,
                        help="Один символ, например BTC/USDT:USDT")
    parser.add_argument("--symbols", type=str, default=None,
                        help="Несколько символов через запятую")

    # Параметры для klines
    parser.add_argument("--interval", type=str, default="1h",
                        help="Таймфрейм: 1m, 5m, 15m, 30m, 1h, 4h, 1d")
    parser.add_argument("--intervals", type=str, default=None,
                        help="Несколько таймфреймов через запятую")

    # Диапазон
    parser.add_argument("--days", type=int, default=None,
                        help="Сколько дней от текущего момента назад")
    parser.add_argument("--start", type=str, default=None,
                        help="Начало диапазона (YYYY-MM-DD или YYYY-MM-DDThh:mm:ss)")
    parser.add_argument("--end", type=str, default=None,
                        help="Конец диапазона (YYYY-MM-DD или YYYY-MM-DDThh:mm:ss)")

    # Orderbook
    parser.add_argument("--depth", type=int, default=50,
                        help="Глубина стакана (1, 50, 200, 500)")

    # Уровень логирования
    parser.add_argument("--verbose", "-v", action="store_true", help="Подробный лог")

    return parser.parse_args()


def resolve_symbols(args: argparse.Namespace) -> list[str]:
    if args.symbol:
        return [args.symbol]
    if args.symbols:
        return [s.strip() for s in args.symbols.split(",")]
    return ["BTC/USDT:USDT"]


def resolve_intervals(args: argparse.Namespace) -> list[str]:
    if args.intervals:
        return [s.strip() for s in args.intervals.split(",")]
    return [args.interval]


def resolve_dates(args: argparse.Namespace) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)

    if args.days:
        start = now - timedelta(days=args.days)
        end = now
        return start, end

    if args.start:
        start = datetime.fromisoformat(args.start)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
    else:
        start = now - timedelta(days=7)

    if args.end:
        end = datetime.fromisoformat(args.end)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
    else:
        end = now

    return start, end


async def main():
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    symbols = resolve_symbols(args)
    intervals = resolve_intervals(args)
    start_dt, end_dt = resolve_dates(args)

    logger.info("DataDownloader start: type=%s symbols=%s intervals=%s",
                args.type, symbols, intervals)
    logger.info("  Range: %s → %s  (%.1f days)",
                start_dt.strftime("%Y-%m-%d %H:%M"),
                end_dt.strftime("%Y-%m-%d %H:%M"),
                (end_dt - start_dt).total_seconds() / 86400)

    async with aiohttp.ClientSession() as session:
        if args.type in ("klines", "all"):
            if len(symbols) > 1 or len(intervals) > 1:
                await batch_download_klines(symbols, intervals, start_dt, end_dt)
            else:
                for symbol in symbols:
                    for interval in intervals:
                        path, count = await download_and_save_klines(session, symbol, interval, start_dt, end_dt)
                        logger.info("Done: %s %s → %d rows in %s", symbol, interval, count, path)

        if args.type in ("trades", "all"):
            for symbol in symbols:
                await download_and_save_trades(session, symbol)

        if args.type in ("orderbook", "all"):
            for symbol in symbols:
                await download_and_save_orderbook(session, symbol)


if __name__ == "__main__":
    asyncio.run(main())
