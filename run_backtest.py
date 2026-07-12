#!/usr/bin/env python3
"""
Backtest Engine — прогон стратегий на исторических данных (snapshots или synthetic).

Использует существующие компоненты:
  - MarketReplayEngine (core/market_replay.py) — для timestamp'ов
  - ContextEngine (context/market_context.py)
  - ConsensusEngine (core/consensus/engine.py)
  - RiskEngine (core/risk/engine.py)
  - OME (core/ome/engine.py)
  - strategies/* (BaseStrategy)

Usage:
    python run_backtest.py [--symbol BTC/USDT:USDT] [--days 30] [--limit 500]
    python run_backtest.py --symbol BTC/USDT:USDT --mode synthetic
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

# ── Project imports ──
from core import Event, SignalResult
from core.market_replay import MarketReplayEngine, MarketSnapshot
from core.features import FeatureEngine, get_feature_engine, reset_feature_engine
from core.features.store import FeatureStore, get_feature_store, reset_feature_store
from context import ContextEngine, MarketContext
from strategies import (
    StrategyEngine,
    StrategyContext,
    StrategyResult,
    get_strategy,
    list_strategies,
    _strategy_registry,
)
from core.consensus import (
    ConsensusEngine,
    ConsensusResult,
    SignalDirection,
    SignalVote,
)
from core.risk import RiskEngine, RiskContext
from core.ome import OME

# ── Auto-discover strategy modules ──
def _discover_strategies():
    """Импортировать все стратегии из strategies/*.py, чтобы сработал @register_strategy."""
    import importlib
    import pkgutil
    import strategies as _strat_pkg
    for _loader, _name, _is_pkg in pkgutil.iter_modules(_strat_pkg.__path__):
        if _name != "__init__" and _name != "base":
            importlib.import_module(f"strategies.{_name}")
_discover_strategies()

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backtest")


# ═══════════════════════════════════════════════════════════
#  SyntheticPriceGenerator
#  Генерирует реалистичный ряд цен (random walk + mean reversion)
# ═══════════════════════════════════════════════════════════


class SyntheticPriceGenerator:
    """Генератор синтетических цен на основе timestamp'ов из БД."""

    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)

    def generate(self, timestamps: list[float], start_price: float = 65000.0) -> list[float]:
        """Сгенерировать цены для списка timestamp'ов."""
        prices = [start_price]
        for i in range(1, len(timestamps)):
            dt = timestamps[i] - timestamps[i - 1]
            # Annualised volatility ~50%, 5-min volatility ~0.7%
            sigma = 0.007 * math.sqrt(dt / 300)  # normalised to 5-min vol
            # Mean reversion force
            prev = prices[-1]
            reversion = (start_price - prev) * 0.001 * dt / 300
            trend = self._rng.gauss(0, sigma) * prev
            price = prev + reversion + trend
            price = max(price, start_price * 0.1)  # floor at 10% of start
            prices.append(round(price, 2))
        return prices


# ═══════════════════════════════════════════════════════════
#  CandleProvider — абстракция источника данных
# ═══════════════════════════════════════════════════════════


class CandleSnapshot:
    """Одна «свеча» для бэктеста (содержит OHLCV + timestamp)."""
    __slots__ = ("ts", "open", "high", "low", "close", "volume", "symbol")

    def __init__(self, ts: float, close: float, symbol: str,
                 volume: float = 100.0, ohlc_pct: float = 0.002):
        self.ts = ts
        self.close = close
        self.open = close * (1 - self._jitter())
        self.high = close * (1 + ohlc_pct * (1 + self._jitter()))
        self.low = close * (1 - ohlc_pct * (1 + self._jitter()))
        self.volume = volume * (1 + self._jitter() * 0.5)
        self.symbol = symbol

    @staticmethod
    def _jitter() -> float:
        return (random.random() - 0.5) * 0.001  # ±0.05%


def load_candles(symbol: str, start_ts: float, end_ts: float,
                 replay_db: str = "market_replay.db",
                 limit: int = 500,
                 mode: str = "auto") -> list[CandleSnapshot]:
    """
    Загрузить свечи для бэктеста.

    Режимы:
      - auto: пробует replay DB, если нет цен → synthetic fallback
      - replay: только replay DB
      - synthetic: только генератор
    """
    candles: list[CandleSnapshot] = []

    if mode in ("auto", "replay"):
        replay = MarketReplayEngine(replay_db)
        window = replay.get_replay(symbol, start_ts, end_ts, limit=limit)

        if window.snapshots:
            prices = [s.price for s in window.snapshots]
            has_real_prices = any(p > 0 for p in prices)
            if has_real_prices:
                logger.info("[data] loaded %d real snapshots from replay DB", len(window.snapshots))
                for snap in window.snapshots:
                    candle = CandleSnapshot(
                        ts=snap.timestamp,
                        close=snap.price,
                        symbol=symbol,
                        volume=snap.volume_24h if snap.volume_24h > 0 else 100.0,
                    )
                    candles.append(candle)
                return candles
            elif mode == "replay":
                logger.error("[data] replay DB has %d snapshots but all have price=0", len(window.snapshots))
                return []

    # Synthetic fallback
    if mode == "auto":
        # Use replay DB timestamps if available, else generate
        if window and window.snapshots:
            timestamps = [s.timestamp for s in window.snapshots]
            logger.info("[data] replay DB has %d timestamps, generating synthetic prices", len(timestamps))
        else:
            # Generate evenly spaced timestamps
            count = min(limit, 200)
            step = (end_ts - start_ts) / count
            timestamps = [start_ts + i * step for i in range(count)]
            logger.info("[data] generating %d synthetic timestamps", count)
    else:
        count = min(limit, 200)
        step = (end_ts - start_ts) / count
        timestamps = [start_ts + i * step for i in range(count)]
        logger.info("[data] generating %d synthetic candles", count)

    gen = SyntheticPriceGenerator(seed=42)
    prices = gen.generate(timestamps, start_price=65000.0)

    for ts, price in zip(timestamps, prices):
        candle = CandleSnapshot(ts=ts, close=price, symbol=symbol, volume=100.0 + random.random() * 50)
        candles.append(candle)

    logger.info("[data] generated %d synthetic candles  price range: %.2f – %.2f",
                len(candles), min(prices), max(prices))
    return candles


# ═══════════════════════════════════════════════════════════
#  MockFeatureStore — эмуляция FeatureStore из candle'й
# ═══════════════════════════════════════════════════════════


class MockFeatureStore:
    """
    FeatureStore, заполняемый из CandleSnapshot'ов в рантайме.

    Для каждой свечи pre-compute'ит фичи, которые нужны стратегиям:
      - ohlcv.{tf}.buffer → список свечеподобных dict'ов
      - candle.movement    → % изменения цены
      - candle.consecutive → количество последовательных свечей одного цвета
    """

    def __init__(self):
        self._data: dict[str, dict[str, Any]] = {}
        self._candle_buffers: dict[str, list[dict]] = {}

    def add(self, candle: CandleSnapshot):
        """Добавить свечу и обновить фичи."""
        sym = candle.symbol
        if sym not in self._candle_buffers:
            self._candle_buffers[sym] = []

        cdict = {
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
            "ts": candle.ts,
        }
        self._candle_buffers[sym].append(cdict)

        if len(self._candle_buffers[sym]) > 100:
            self._candle_buffers[sym] = self._candle_buffers[sym][-100:]

        buf = self._candle_buffers[sym]
        features: dict[str, Any] = {}

        features["ohlcv.1m.buffer"] = buf
        features["ohlcv.5m.buffer"] = buf

        # candle.movement
        if len(buf) >= 2:
            prev_close = buf[-2]["close"]
            curr_close = buf[-1]["close"]
            features["candle.movement"] = ((curr_close - prev_close) / prev_close * 100) if prev_close > 0 else 0.0
        else:
            features["candle.movement"] = 0.0

        # candle.consecutive
        features["candle.consecutive"] = self._calc_consecutive(buf)

        # Regime (упрощённо)
        if len(buf) >= 20:
            prices = [c["close"] for c in buf[-20:]]
            sma20 = sum(prices) / len(prices) if prices else 1.0
            if sma20 > 0:
                features["regime.trend"] = "bull" if prices[-1] > sma20 else ("bear" if prices[-1] < sma20 else "flat")
                vol = (max(prices) - min(prices)) / sma20 * 100
                features["vol.regime"] = "high" if vol > 5 else ("normal" if vol > 2 else "low")
                features["vol.regime_score"] = min(100, vol * 10)
                features["regime.volatility_state"] = "expansion" if vol > 4 else "stable"
            else:
                features["regime.trend"] = "flat"
                features["vol.regime"] = "normal"
                features["vol.regime_score"] = 30
                features["regime.volatility_state"] = "stable"
        else:
            features["regime.trend"] = "flat"
            features["vol.regime"] = "normal"
            features["vol.regime_score"] = 30
            features["regime.volatility_state"] = "stable"

        self._data[sym] = features

    def _calc_consecutive(self, buf: list[dict]) -> dict:
        """Сколько свечей подряд одного цвета (close >= prev close = green)."""
        if len(buf) < 3:
            return {"count": 0, "direction": "neutral", "strength": 0}
        count = 0
        direction = "neutral"
        for i in range(len(buf) - 1, 0, -1):
            close_rise = buf[i]["close"] >= buf[i - 1]["close"]
            if i == len(buf) - 1:
                direction = "green" if close_rise else "red"
                count = 1
            else:
                prev_rise = buf[i - 1]["close"] >= buf[i - 2]["close"]
                if close_rise == prev_rise:
                    count += 1
                else:
                    break
        return {"count": count, "direction": direction, "strength": min(100, count * 10)}

    async def get(self, symbol: str, name: str) -> Any | None:
        return self._data.get(symbol, {}).get(name, None)

    async def get_feature(self, symbol: str, name: str) -> Any | None:
        """Совместимость с FeatureEngine API."""
        return await self.get(symbol, name)


# ═══════════════════════════════════════════════════════════
#  BacktestRunner
# ═══════════════════════════════════════════════════════════


class BacktestRunner:
    """Основной класс бэктеста."""

    def __init__(self, capital: float = 1000.0, risk_pct: float = 0.01, mode: str = "auto"):
        self.capital = capital
        self.risk_pct = risk_pct
        self.mode = mode

        self.mock_store = MockFeatureStore()
        self.context_engine = ContextEngine(feature_engine=None)
        self.consensus_engine = ConsensusEngine(shadow=True)
        self.risk_engine = RiskEngine(shadow=True)
        self.ome = OME(shadow=True, capital=capital, risk_pct=risk_pct)

        self.strategies: list[tuple[str, Any]] = []
        self.trades: list[dict] = []
        self.signals: list[dict] = []

    def load_strategies(self, names: list[str] | None = None):
        if names is None:
            names = list(_strategy_registry.keys())
        for name in names:
            cls = _strategy_registry.get(name)
            if cls is None:
                logger.warning("[backtest] strategy '%s' not found", name)
                continue
            instance = cls()
            self.strategies.append((name, instance))
            logger.info("[backtest] loaded strategy '%s'", name)
        logger.info("[backtest] %d strategies loaded", len(self.strategies))

    async def run(
        self,
        symbol: str,
        start_ts: float,
        end_ts: float,
        replay_db: str = "market_replay.db",
        limit: int = 500,
    ) -> dict[str, Any]:
        logger.info(
            "Backtest %s  %s → %s  (limit=%d, mode=%s)",
            symbol,
            datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
            datetime.fromtimestamp(end_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
            limit,
            self.mode,
        )

        # 1. Load candles
        candles = load_candles(
            symbol=symbol,
            start_ts=start_ts,
            end_ts=end_ts,
            replay_db=replay_db,
            limit=limit,
            mode=self.mode,
        )
        if not candles:
            return {"error": f"No data for {symbol}", "trades": 0}

        logger.info("[backtest] loaded %d candles (%.1f hours)",
                     len(candles), (candles[-1].ts - candles[0].ts) / 3600)

        # 2. Pipeline
        for i, candle in enumerate(candles):
            # Update mock store
            self.mock_store.add(candle)

            # Check SL/TP for open positions
            self._check_sl_tp(symbol, candle.close)

            # Build context
            ctx = MarketContext(
                symbol=symbol,
                trend="flat",
                volatility="normal",
                session="asia",
                session_name="Backtest",
            )
            # Populate from features
            trend_val = await self._safe_get(symbol, "regime.trend")
            if trend_val:
                ctx.trend = trend_val
            vol_val = await self._safe_get(symbol, "vol.regime")
            if vol_val:
                ctx.volatility = vol_val
            score_val = await self._safe_get(symbol, "vol.regime_score")
            if score_val is not None:
                ctx.regime_score = float(score_val)
            vol_state = await self._safe_get(symbol, "regime.volatility_state")
            if vol_state:
                ctx.volatility_state = vol_state

            # Run strategies
            votes: list[SignalVote] = []
            for name, strategy in self.strategies:
                try:
                    strat_ctx = StrategyContext(
                        symbol=symbol,
                        features=self.mock_store,
                        context=ctx,
                        exchange="bybit",
                    )
                    result = await strategy.evaluate(strat_ctx)
                except Exception as e:
                    logger.debug("[backtest] strategy '%s' error: %s", name, e, exc_info=True)
                    continue

                if result is None:
                    continue

                if not strategy.can_send(result.symbol, result.score, result.direction):
                    continue

                logger.info(
                    "[backtest] SIGNAL %s %s score=%.0f dir=%s",
                    result.strategy_name, result.symbol, result.score, result.direction,
                )

                self.signals.append({
                    "ts": candle.ts,
                    "symbol": result.symbol,
                    "strategy": result.strategy_name,
                    "direction": result.direction,
                    "score": result.score,
                    "price": candle.close,
                })

                sd = SignalDirection.BUY if result.direction == "buy" else (
                    SignalDirection.SELL if result.direction == "sell" else SignalDirection.NEUTRAL
                )
                vote = SignalVote(
                    strategy_name=result.strategy_name,
                    direction=sd,
                    confidence=0.5,
                    score=result.score,
                    extra={"symbol": result.symbol, "price": candle.close},
                )
                votes.append(vote)

            if not votes:
                continue

            consensus = self.consensus_engine.evaluate(votes)
            # Backtest mode: accept 1+ strategy (unlike production requiring >= 2)
            if consensus.direction == SignalDirection.NEUTRAL or consensus.confidence <= 0.3:
                logger.debug(
                    "[backtest] consensus skip %s: dir=%s conf=%.3f votes=%d",
                    symbol, consensus.direction.value, consensus.confidence, consensus.total_votes,
                )
                continue

            logger.info(
                "[backtest] CONSENSUS %s → %s score=%.1f conf=%.3f votes=%d",
                symbol, consensus.direction.value, consensus.score, consensus.confidence,
                consensus.total_votes,
            )

            # Skip if position already open
            existing = self.ome.tracker.get(symbol)
            if existing is not None:
                logger.debug("[backtest] position already open for %s, skipping new signal", symbol)
                continue

            risk_ctx = RiskContext(
                symbol=symbol,
                exchange="bybit",
                price=candle.close,
                signal_name=consensus.direction.value,
                signal_score=consensus.score,
                signal_direction=consensus.direction.value,
            )
            risk_result = await self.risk_engine.evaluate(risk_ctx)
            if risk_result.blocked:
                logger.debug("[backtest] risk BLOCK %s: %s", symbol, risk_result.summary)
                continue

            side = consensus.direction.value
            atr_pct = 0.02

            order_result = self.ome.execute_signal(
                symbol=symbol,
                side=side,
                price=candle.close,
                atr=candle.close * atr_pct,
            )

            if "error" in order_result:
                logger.debug("[backtest] OME skip %s: %s", symbol, order_result["error"])
                continue

            pos = order_result.get("position", {})
            risk = order_result.get("risk", {})
            sizer = order_result.get("sizer", {})
            trade = {
                "ts": candle.ts,
                "symbol": symbol,
                "side": side,
                "price": candle.close,
                "qty": sizer.get("qty", 0),
                "stop_loss": risk.get("stop_loss"),
                "take_profit": risk.get("take_profit"),
                "risk_amount": sizer.get("risk_amount", 0),
                "consensus_score": consensus.score,
                "strategy": votes[0].strategy_name if votes else "?",
            }
            self.trades.append(trade)
            logger.info(
                "[backtest] TRADE %s %s @ %.2f qty=%.4f sl=%.2f tp=%.2f",
                symbol, side.upper(), candle.close,
                trade["qty"], trade["stop_loss"] or 0, trade["take_profit"] or 0,
            )

        total_pnl = self.ome.total_pnl
        stats = self._calculate_stats()

        # Force close any remaining position at last candle price
        open_positions = self.ome.tracker.all()
        for pos in open_positions:
            side = pos.side.value
            last_price = candles[-1].close
            pnl = (last_price - pos.entry_price) * pos.size if side == "buy" else (pos.entry_price - last_price) * pos.size
            self.ome.tracker.close(pos.symbol, price=last_price)
            if self.trades:
                self.trades[-1]["exit_price"] = last_price
                self.trades[-1]["exit_reason"] = "END"
                self.trades[-1]["pnl"] = round(pnl, 2)
                logger.info(
                    "[backtest] FORCE CLOSE %s @ %.2f  PnL=%.4f",
                    pos.symbol, last_price, pnl,
                )
        stats = self._calculate_stats()

        logger.info("[backtest] DONE %s: %d trades, PnL=%.2f, win_rate=%.1f%%",
                    symbol, stats.get("total_trades", 0), stats.get("total_pnl", 0),
                    stats.get("win_rate", 0))
        return stats

    def _check_sl_tp(self, symbol: str, price: float):
        """Проверить SL/TP для открытой позиции и закрыть при достижении."""
        pos = self.ome.tracker.get(symbol)
        if pos is None:
            return

        # BUY position: stop_loss below entry, tp above
        if pos.side.value == "buy":
            hit_sl = pos.stop_loss > 0 and price <= pos.stop_loss
            hit_tp = pos.take_profit > 0 and price >= pos.take_profit
        else:
            hit_sl = pos.stop_loss > 0 and price >= pos.stop_loss
            hit_tp = pos.take_profit > 0 and price <= pos.take_profit

        if hit_sl or hit_tp:
            exit_reason = "SL" if hit_sl else "TP"
            # Manually compute PnL (unrealized_pnl is a dataclass field, not computed)
            side = pos.side.value
            pnl = (price - pos.entry_price) * pos.size if side == "buy" else (pos.entry_price - price) * pos.size
            self.ome.tracker.close(symbol, price=price)
            if self.trades:
                self.trades[-1]["exit_price"] = price
                self.trades[-1]["exit_reason"] = exit_reason
                self.trades[-1]["pnl"] = round(pnl, 2)
                logger.info(
                    "[backtest] CLOSE %s %s @ %.2f  PnL=%.4f",
                    exit_reason, symbol, price, pnl,
                )

    async def _safe_get(self, symbol: str, name: str) -> Any | None:
        try:
            return await self.mock_store.get(symbol, name)
        except Exception:
            return None

    def _calculate_stats(self) -> dict[str, Any]:
        if not self.trades:
            return {
                "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
                "win_rate": 0, "total_pnl": 0, "sharpe_ratio": 0,
                "max_drawdown": 0, "profit_factor": 0,
                "return_pct": 0, "avg_pnl": 0, "avg_win": 0, "avg_loss": 0,
                "capital": self.capital, "message": "No trades",
            }

        # Realised PnL from closed trades + unrealised from open positions
        pnl_per_trade = []
        for trade in self.trades:
            pnl = trade.get("pnl")
            if pnl is None:
                # Still open — estimate from risk_amount (1:2 R:R expiry)
                risk_amt = trade.get("risk_amount", 0)
                pnl = risk_amt * 2 if trade.get("side") == "buy" else -risk_amt * 2
            pnl_per_trade.append(pnl)
        total_pnl = sum(pnl_per_trade)

        # Add unrealised PnL from still-open positions
        open_positions = self.ome.tracker.all()
        for pos in open_positions:
            total_pnl += pos.unrealized_pnl

        df = pd.DataFrame({"pnl": pnl_per_trade})
        total_pnl = float(df["pnl"].sum())
        winning = int((df["pnl"] > 0).sum())
        losing = int((df["pnl"] <= 0).sum())
        total = len(df)
        win_rate = (winning / total * 100) if total > 0 else 0

        returns = df["pnl"] / self.capital
        sharpe = float(returns.mean() / returns.std() * math.sqrt(288)) if returns.std() > 0 else 0

        cum_pnl = df["pnl"].cumsum()
        rolling_max = cum_pnl.cummax()
        drawdown = rolling_max - cum_pnl
        max_dd = float(drawdown.max())

        gross_profit = float(df[df["pnl"] > 0]["pnl"].sum()) if winning > 0 else 0
        gross_loss = float(abs(df[df["pnl"] <= 0]["pnl"].sum())) if losing > 0 else 0
        profit_factor = float(gross_profit / gross_loss) if gross_loss > 0 else float("inf")

        return {
            "total_trades": total,
            "winning_trades": winning,
            "losing_trades": losing,
            "win_rate": round(win_rate, 1),
            "total_pnl": round(total_pnl, 4),
            "sharpe_ratio": round(sharpe, 4),
            "max_drawdown": round(max_dd, 4),
            "profit_factor": "inf" if profit_factor == float("inf") else round(profit_factor, 2),
            "return_pct": round(total_pnl / self.capital * 100, 2),
            "avg_pnl": round(float(df["pnl"].mean()), 4),
            "avg_win": round(float(df[df["pnl"] > 0]["pnl"].mean()), 4) if winning > 0 else 0,
            "avg_loss": round(float(df[df["pnl"] < 0]["pnl"].mean()), 4) if losing > 0 else 0,
            "capital": self.capital,
        }

    def export_trades(self, filepath: str):
        if self.trades:
            pd.DataFrame(self.trades).to_csv(filepath, index=False)
            logger.info("Trades exported to %s", filepath)

    def export_signals(self, filepath: str):
        if self.signals:
            pd.DataFrame(self.signals).to_csv(filepath, index=False)
            logger.info("Signals exported to %s", filepath)

    def export_stats(self, filepath: str, stats: dict):
        with open(filepath, "w") as f:
            json.dump(stats, f, indent=2, default=str)
        logger.info("Stats exported to %s", filepath)


# ═══════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════


def parse_args():
    parser = argparse.ArgumentParser(description="Backtest Engine for crypto-screener-v2")
    parser.add_argument("--symbol", default="BTC/USDT:USDT",
                        help="Symbol (default: BTC/USDT:USDT)")
    parser.add_argument("--days", type=int, default=1,
                        help="Lookback days (default: 1)")
    parser.add_argument("--limit", type=int, default=300,
                        help="Max candles (default: 300)")
    parser.add_argument("--replay-db", default="market_replay.db",
                        help="Path to replay DB (default: market_replay.db)")
    parser.add_argument("--capital", type=float, default=1000.0,
                        help="Initial capital (default: 1000 USDT)")
    parser.add_argument("--risk-pct", type=float, default=0.01,
                        help="Risk per trade (default: 0.01 = 1%%)")
    parser.add_argument("--strategy", nargs="*", default=None,
                        help="Strategy name(s) (default: all)")
    parser.add_argument("--mode", choices=["auto", "synthetic", "replay"], default="auto",
                        help="Data mode: auto (DB→fallback to synthetic), synthetic, replay (default: auto)")
    parser.add_argument("--list-strategies", action="store_true",
                        help="List available strategies")
    return parser.parse_args()


async def main():
    args = parse_args()

    if args.list_strategies:
        available = list_strategies()
        print("\n=== Available Strategies ===")
        for name, meta in available.items():
            print(f"  {name:20s}  {meta.description}")
        print()
        return

    runner = BacktestRunner(capital=args.capital, risk_pct=args.risk_pct, mode=args.mode)
    runner.load_strategies(args.strategy)
    if not runner.strategies:
        logger.error("No strategies loaded — aborting")
        sys.exit(1)

    now = time.time()
    start_ts = now - args.days * 86400
    end_ts = now

    stats = await runner.run(
        symbol=args.symbol,
        start_ts=start_ts,
        end_ts=end_ts,
        replay_db=args.replay_db,
        limit=args.limit,
    )

    # Вывод
    print("\n" + "=" * 55)
    print("  BACKTEST RESULTS")
    print("=" * 55)
    if "error" in stats:
        print(f"  ERROR: {stats['error']}")
    else:
        print(f"  Symbol:         {args.symbol}")
        print(f"  Period:         {args.days}d  ({args.limit} candles)")
        print(f"  Mode:           {args.mode}")
        print(f"  Strategies:     {', '.join(n for n, _ in runner.strategies)}")
        print(f"  Capital:        ${args.capital:.2f}")
        print(f"  Risk/trade:     {args.risk_pct*100:.1f}%")
        print(f"  ─────────────────────────────")
        print(f"  Total Trades:   {stats['total_trades']}")
        print(f"  Win Rate:       {stats['win_rate']:.1f}%")
        print(f"  Total PnL:      ${stats['total_pnl']:.2f}")
        print(f"  Return:         {stats['return_pct']:.2f}%")
        print(f"  Sharpe Ratio:   {stats['sharpe_ratio']:.4f}")
        print(f"  Max Drawdown:   ${stats['max_drawdown']:.2f}")
        print(f"  Profit Factor:  {stats['profit_factor']}")
        print(f"  Avg PnL:        ${stats['avg_pnl']:.4f}")
        if stats.get("winning_trades"):
            print(f"  Avg Win:        ${stats['avg_win']:.4f}")
        if stats.get("losing_trades"):
            print(f"  Avg Loss:       ${stats['avg_loss']:.4f}")
    print("=" * 55)
    print()

    # Экспорт
    runner.export_trades("backtest_trades.csv")
    runner.export_signals("backtest_signals.csv")
    runner.export_stats("backtest_stats.json", stats)

    print("Files saved:")
    print("  backtest_trades.csv")
    print("  backtest_signals.csv")
    print("  backtest_stats.json")


if __name__ == "__main__":
    asyncio.run(main())
