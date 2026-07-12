"""
Market Analysis — Expected Move (#16), Risk Meter (#17), Noise Filter (#18).

Общий модуль: ATR, волатильность, ликвидность, спред.
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field

from core import SignalResult

logger = logging.getLogger(__name__)

# ── Константы ──────────────────────────────────────────────
ATR_PERIOD = 14
VOLATILITY_WINDOW = 24       # часов для HV
NOISE_THRESHOLD = 70          # % — считаем рынок шумным
HIGH_RISK_THRESHOLD = 75      # % — риск HIGH
MEDIUM_RISK_THRESHOLD = 40    # % — риск MEDIUM
EXPECTED_MOVE_CONFIDENCE = 0.68  # 68% (1σ)


@dataclass
class ExpectedMoveSnapshot:
    symbol: str = ""
    current_price: float = 0.0
    expected_move_pct: float = 0.0   # %
    expected_move_abs: float = 0.0   # в USD
    probability: float = EXPECTED_MOVE_CONFIDENCE
    atr: float = 0.0
    atr_pct: float = 0.0
    hist_volatility: float = 0.0     # 24ч
    direction: str = "neutral"
    upper_target: float = 0.0
    lower_target: float = 0.0
    description: str = ""


@dataclass
class RiskMeterSnapshot:
    symbol: str = ""
    risk_level: str = "LOW"          # LOW / MEDIUM / HIGH
    risk_score: float = 0.0           # 0-100
    atr_score: float = 0.0
    liquidity_score: float = 0.0
    spread_score: float = 0.0
    oi_score: float = 0.0
    atr_pct: float = 0.0
    spread_pct: float = 0.0
    bid_ask_spread: float = 0.0
    volume_24h: float = 0.0
    description: str = ""


@dataclass
class NoiseFilterSnapshot:
    symbol: str = ""
    noise_pct: float = 0.0            # 0-100
    is_noisy: bool = False
    candle_count: int = 0
    directional_candles: int = 0
    range_pct: float = 0.0            # % диапазон последних N свечей
    body_to_wick_ratio: float = 0.0   # отношение тела к теням
    consecutive_noise: int = 0
    description: str = ""


# ── Вспомогательные функции ─────────────────────────────────

def _calc_atr(candles: list[dict], period: int = ATR_PERIOD) -> float:
    """Рассчитать ATR из свечей."""
    if not candles or len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, min(len(candles), period + 1)):
        high = float(candles[i].get("high", 0))
        low = float(candles[i].get("low", 0))
        prev_close = float(candles[i - 1].get("close", 0))
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if not trs:
        return 0.0
    return sum(trs) / len(trs)


def _calc_hist_volatility(candles: list[dict], window: int = VOLATILITY_WINDOW) -> float:
    """Годовая историческая волатильность (из часовых свечей)."""
    if not candles or len(candles) < window:
        return 0.0
    returns = []
    for i in range(1, min(len(candles), window + 1)):
        c0 = float(candles[i - 1].get("close", 0))
        c1 = float(candles[i].get("close", 0))
        if c0 > 0 and c1 > 0:
            returns.append(math.log(c1 / c0))
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    daily_vol = math.sqrt(var)
    return daily_vol * math.sqrt(365) * 100  # годовая %


def _calc_noise(candles: list[dict], window: int = 24) -> float:
    """Процент шумовых свечей (тело < 30% диапазона)."""
    if not candles or len(candles) < window:
        return 0.0
    noisy = 0
    total = 0
    for c in candles[-window:]:
        high = float(c.get("high", 0))
        low = float(c.get("low", 0))
        close = float(c.get("close", 0))
        open_ = float(c.get("open", 0))
        rng = high - low
        if rng <= 0:
            continue
        body = abs(close - open_)
        if body / rng < 0.3:
            noisy += 1
        total += 1
    return (noisy / max(total, 1)) * 100


# ── Expected Move Engine ────────────────────────────────────

class ExpectedMoveEngine:
    """Ожидаемое движение цены на основе ATR + HV."""

    def __init__(self):
        self._cache: dict[str, ExpectedMoveSnapshot] = {}

    def analyze(self, symbol: str, candles_5m: list[dict] | None, candles_1h: list[dict] | None,
                current_price: float | None = None) -> ExpectedMoveSnapshot:
        atr = _calc_atr(candles_5m or []) if candles_5m else 0.0
        hv = _calc_hist_volatility(candles_1h or []) if candles_1h else 0.0
        price = current_price or 0.0

        atr_pct = (atr / price * 100) if price > 0 else 0.0
        expected_move = max(atr_pct * 1.5, hv / math.sqrt(365) * 1.5) if hv > 0 else atr_pct * 1.5

        upper = price * (1 + expected_move / 100)
        lower = price * (1 - expected_move / 100)

        snap = ExpectedMoveSnapshot(
            symbol=symbol,
            current_price=round(price, 2),
            expected_move_pct=round(expected_move, 2),
            expected_move_abs=round(price * expected_move / 100, 2),
            probability=EXPECTED_MOVE_CONFIDENCE,
            atr=round(atr, 2),
            atr_pct=round(atr_pct, 4),
            hist_volatility=round(hv, 2),
            direction="buy" if atr > 0 else "neutral",
            upper_target=round(upper, 2),
            lower_target=round(lower, 2),
            description=f"Expected ±{expected_move:.2f}% (1σ, {EXPECTED_MOVE_CONFIDENCE*100:.0f}% confidence)",
        )
        self._cache[symbol] = snap
        return snap

    def to_signal(self, symbol: str, direction: str = "buy") -> SignalResult | None:
        snap = self._cache.get(symbol)
        if not snap or snap.expected_move_pct < 0.5:
            return None
        score = min(snap.expected_move_pct * 5 + 40, 85)
        if score < 45:
            return None
        return SignalResult(
            signal_name="expected_move",
            symbol=symbol,
            exchange="bybit",
            score=round(score, 0),
            direction=direction,
            meta={
                "expected_move_pct": snap.expected_move_pct,
                "expected_move_abs": snap.expected_move_abs,
                "probability": snap.probability,
                "atr": snap.atr,
                "atr_pct": snap.atr_pct,
                "hist_volatility": snap.hist_volatility,
                "upper_target": snap.upper_target,
                "lower_target": snap.lower_target,
                "current_price": snap.current_price,
                "description": snap.description,
            },
            ts=time.time(),
            cooldown=3600,
        )


# ── Risk Meter Engine ───────────────────────────────────────

class RiskMeterEngine:
    """Композитный риск: ATR + ликвидность + спред."""

    def __init__(self):
        self._cache: dict[str, RiskMeterSnapshot] = {}

    def analyze(self, symbol: str, candles_5m: list[dict] | None,
                ticker: dict | None = None, orderbook: dict | None = None) -> RiskMeterSnapshot:
        # ATR score
        atr = _calc_atr(candles_5m or [])
        price = float(ticker.get("last_price", 0)) if ticker else 0
        atr_pct = (atr / price * 100) if price > 0 else 0
        atr_score = min(atr_pct * 10, 100)  # ATR > 10% → 100

        # Spread score
        bid = float(ticker.get("bid_price", 0)) if ticker else 0
        ask = float(ticker.get("ask_price", 0)) if ticker else 0
        spread = ask - bid if ask > 0 and bid > 0 else 0
        spread_pct = (spread / price * 100) if price > 0 else 0
        spread_score = min(spread_pct * 100, 100)  # spread > 1% → 100

        # Volume + Liquidity score
        volume_24h = float(ticker.get("volume_24h", 0)) if ticker else 0
        liq_score = max(0, 100 - math.log10(max(volume_24h, 1)) * 10)

        # OI score (if available)
        oi = float(ticker.get("open_interest", 0)) if ticker else 0
        oi_score = max(0, 100 - math.log10(max(oi, 1)) * 10) if oi > 0 else 50

        # Composite
        risk_score = atr_score * 0.35 + spread_score * 0.25 + liq_score * 0.25 + oi_score * 0.15

        if risk_score >= HIGH_RISK_THRESHOLD:
            level = "HIGH"
        elif risk_score >= MEDIUM_RISK_THRESHOLD:
            level = "MEDIUM"
        else:
            level = "LOW"

        snap = RiskMeterSnapshot(
            symbol=symbol,
            risk_level=level,
            risk_score=round(risk_score, 0),
            atr_score=round(atr_score, 0),
            liquidity_score=round(liq_score, 0),
            spread_score=round(spread_score, 0),
            oi_score=round(oi_score, 0),
            atr_pct=round(atr_pct, 4),
            spread_pct=round(spread_pct, 4),
            bid_ask_spread=round(spread, 4),
            volume_24h=round(volume_24h, 0),
            description=f"Risk: {level} ({risk_score:.0f}/100) — ATR {atr_score:.0f} / Spread {spread_score:.0f} / Liq {liq_score:.0f}",
        )
        self._cache[symbol] = snap
        return snap

    def to_signal(self, symbol: str) -> SignalResult | None:
        snap = self._cache.get(symbol)
        if not snap or snap.risk_level == "LOW":
            return None
        return SignalResult(
            signal_name="risk_meter",
            symbol=symbol,
            exchange="bybit",
            score=round(snap.risk_score, 0) if snap.risk_level == "HIGH" else round(snap.risk_score * 0.7, 0),
            direction="neutral",
            meta={
                "risk_level": snap.risk_level,
                "risk_score": snap.risk_score,
                "atr_score": snap.atr_score,
                "liquidity_score": snap.liquidity_score,
                "spread_score": snap.spread_score,
                "oi_score": snap.oi_score,
                "atr_pct": snap.atr_pct,
                "spread_pct": snap.spread_pct,
                "volume_24h": snap.volume_24h,
                "description": snap.description,
            },
            ts=time.time(),
            cooldown=7200,
        )


# ── Noise Filter Engine ─────────────────────────────────────

class NoiseFilterEngine:
    """Определение шумового рынка (пилит)."""

    def __init__(self):
        self._cache: dict[str, NoiseFilterSnapshot] = {}
        self._consecutive: dict[str, int] = defaultdict(int)

    def analyze(self, symbol: str, candles_5m: list[dict] | None) -> NoiseFilterSnapshot:
        if not candles_5m:
            snap = NoiseFilterSnapshot(
                symbol=symbol, noise_pct=0, is_noisy=False, description="No data",
            )
            self._cache[symbol] = snap
            return snap

        candle_window = 24
        noise_pct = _calc_noise(candles_5m, window=candle_window)

        # Body-to-wick ratio
        bodies = []
        wicks = []
        for c in candles_5m[-candle_window:]:
            high = float(c.get("high", 0))
            low = float(c.get("low", 0))
            close = float(c.get("close", 0))
            open_ = float(c.get("open", 0))
            rng = high - low
            if rng <= 0:
                continue
            body = abs(close - open_)
            bodies.append(body)
            wicks.append(rng - body)

        avg_body = sum(bodies) / max(len(bodies), 1)
        avg_wick = sum(wicks) / max(len(wicks), 1)
        body_to_wick = avg_body / max(avg_wick, 0.001)

        # Range %
        prices = [float(c.get("close", 0)) for c in candles_5m[-candle_window:] if c.get("close")]
        price_range = (max(prices) - min(prices)) / max(min(prices), 0.001) * 100 if prices else 0

        is_noisy = noise_pct >= NOISE_THRESHOLD
        if is_noisy:
            self._consecutive[symbol] += 1
        else:
            self._consecutive[symbol] = 0

        snap = NoiseFilterSnapshot(
            symbol=symbol,
            noise_pct=round(noise_pct, 0),
            is_noisy=is_noisy,
            candle_count=min(len(candles_5m), candle_window),
            directional_candles=0,
            range_pct=round(price_range, 2),
            body_to_wick_ratio=round(body_to_wick, 2),
            consecutive_noise=self._consecutive[symbol],
            description=f"Noise {noise_pct:.0f}% — {'⚠ Skip' if is_noisy else '✅ Tradable'} ({body_to_wick:.2f} B/W)",
        )
        self._cache[symbol] = snap
        return snap

    def to_signal(self, symbol: str) -> SignalResult | None:
        snap = self._cache.get(symbol)
        if not snap or not snap.is_noisy or snap.consecutive_noise < 3:
            return None
        return SignalResult(
            signal_name="noise_filter",
            symbol=symbol,
            exchange="bybit",
            score=round(max(40, 100 - snap.noise_pct * 0.5), 0),
            direction="neutral",
            meta={
                "noise_pct": snap.noise_pct,
                "is_noisy": snap.is_noisy,
                "body_to_wick_ratio": snap.body_to_wick_ratio,
            },
            ts=time.time(),
            cooldown=7200,
        )


# ── Singleton engine functions (thread-safe) ──

import threading

_expected_move_engine: ExpectedMoveEngine | None = None
_risk_meter_engine: RiskMeterEngine | None = None
_noise_filter_engine: NoiseFilterEngine | None = None
_engine_lock = threading.Lock()


def get_expected_move_engine() -> ExpectedMoveEngine:
    global _expected_move_engine
    if _expected_move_engine is None:
        with _engine_lock:
            if _expected_move_engine is None:
                _expected_move_engine = ExpectedMoveEngine()
    return _expected_move_engine


def get_risk_meter_engine() -> RiskMeterEngine:
    global _risk_meter_engine
    if _risk_meter_engine is None:
        with _engine_lock:
            if _risk_meter_engine is None:
                _risk_meter_engine = RiskMeterEngine()
    return _risk_meter_engine


def get_noise_filter_engine() -> NoiseFilterEngine:
    global _noise_filter_engine
    if _noise_filter_engine is None:
        with _engine_lock:
            if _noise_filter_engine is None:
                _noise_filter_engine = NoiseFilterEngine()
    return _noise_filter_engine
