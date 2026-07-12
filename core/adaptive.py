"""
Adaptive Thresholds — динамические пороги на основе волатильности, сессии и символа.

Вместо фиксированных констант (Z_THRESHOLD = 3.0, SPD_PCT = 0.001):
    threshold = adaptive.get("volume_z", symbol="BTC/USDT:USDT")

Как это работает:
1. VolatilityTracker считает ATR(14) на 1m/5m/15m для каждого символа
2. AdaptiveThresholds выдаёт порог = base_value × vol_mult × session_mult
3. В спокойное время пороги выше → меньше ложных сигналов
4. В волатильное время пороги ниже → не пропускаем реальные всплески
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field

from core.session import get_current_session_type, SessionType, \
    SESSION_THRESHOLD_MULTIPLIERS as SESSION_MULTIPLIERS_SESSION

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
#  Типы волатильности
# ──────────────────────────────────────────────

VOLATILITY_LOW = "low"        # ATR < 0.5% — штиль
VOLATILITY_NORMAL = "normal"  # ATR 0.5-1.5% — обычный рынок
VOLATILITY_HIGH = "high"      # ATR 1.5-3% — волатильный
VOLATILITY_EXTREME = "extreme"  # ATR > 3% — экстремальный (новости, ликвидации)

# Факторы для каждого режима — насколько поднимать/опускать пороги
VOL_MULTIPLIERS = {
    VOLATILITY_LOW: 1.4,      # тихо → порог ВЫШЕ (меньше сигналов)
    VOLATILITY_NORMAL: 1.0,   # норма → базовый порог
    VOLATILITY_HIGH: 0.7,     # волатильно → порог НИЖЕ (больше сигналов)
    VOLATILITY_EXTREME: 0.5,  # экстрим → очень низкий порог
}

# ──────────────────────────────────────────────
#  Сессия (через SessionEngine)
# ──────────────────────────────────────────────

SESSION_MULTIPLIERS = SESSION_MULTIPLIERS_SESSION


def get_current_session() -> str:
    """
    Определить текущую торговую сессию через SessionEngine.
    Заменяет статическую заглушку.
    """
    st = get_current_session_type()
    return st.value


# ──────────────────────────────────────────────
#  Волатильность (ATR)
# ──────────────────────────────────────────────

def calc_atr(candles: list[dict], period: int = 14) -> float:
    """ATR = среднее истинных диапазонов за period свечей."""
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(-period, 0):
        try:
            prev_close = float(candles[i - 1].get("close", 0))
            high = float(candles[i].get("high", 0))
            low = float(candles[i].get("low", 0))
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)
        except (TypeError, ValueError):
            continue
    if not trs:
        return 0.0
    return sum(trs) / len(trs)


def get_volatility_regime(atr_pct: float) -> str:
    """Определить режим волатильности по ATR%."""
    if atr_pct < 0.3:
        return VOLATILITY_LOW
    elif atr_pct < 1.0:
        return VOLATILITY_NORMAL
    elif atr_pct < 2.5:
        return VOLATILITY_HIGH
    else:
        return VOLATILITY_EXTREME


# ──────────────────────────────────────────────
#  VolatilityTracker — следит за ATR всех символов
# ──────────────────────────────────────────────

@dataclass
class VolatilitySnapshot:
    atr_1m: float = 0.0
    atr_5m: float = 0.0
    atr_15m: float = 0.0
    atr_pct: float = 0.0      # ATR% от цены
    regime: str = VOLATILITY_NORMAL
    price: float = 0.0
    updated_at: float = 0.0


class VolatilityTracker:
    """
    Следит за ATR для каждого символа.
    Обновляется при поступлении новых свечей.
    """

    def __init__(self):
        self._states: dict[str, VolatilitySnapshot] = defaultdict(VolatilitySnapshot)

    def update(self, symbol: str, candles_1m: list[dict], candles_5m: list[dict] | None = None,
               candles_15m: list[dict] | None = None, price: float = 0.0):
        """Обновить ATR для символа на основе буфера свечей."""
        snap = self._states[symbol]
        snap.atr_1m = calc_atr(candles_1m, 14) if candles_1m else snap.atr_1m
        snap.atr_5m = calc_atr(candles_5m or [], 14) if candles_5m else snap.atr_5m
        snap.atr_15m = calc_atr(candles_15m or [], 14) if candles_15m else snap.atr_15m

        # ATR как % от цены (берём 15m ATR, если есть, иначе 5m, иначе 1m)
        ref_atr = snap.atr_15m or snap.atr_5m or snap.atr_1m
        ref_price = float(price) if price else float(snap.price)  # гарантируем float
        snap.atr_pct = (ref_atr / ref_price * 100) if ref_price > 0 else 0.0
        snap.regime = get_volatility_regime(snap.atr_pct)
        snap.price = ref_price
        snap.updated_at = time.time()

    def get(self, symbol: str) -> VolatilitySnapshot | None:
        snap = self._states.get(symbol)
        if snap and snap.updated_at > 0:
            return snap
        return None

    def get_regime(self, symbol: str) -> str:
        snap = self.get(symbol)
        if snap:
            return snap.regime
        return VOLATILITY_NORMAL

    def get_multiplier(self, symbol: str) -> float:
        """Множитель порога на основе волатильности символа."""
        regime = self.get_regime(symbol)
        return VOL_MULTIPLIERS.get(regime, 1.0)

    def get_atr_pct(self, symbol: str) -> float:
        snap = self.get(symbol)
        return snap.atr_pct if snap else 0.0


# ──────────────────────────────────────────────
#  SymbolProfile — пер-символьная калибровка
# ──────────────────────────────────────────────

SYMBOL_BASE_MULTIPLIERS = {
    # BTC и ETH — самые ликвидные, их всплески значимее
    "BTC/USDT:USDT": 1.0,
    "ETH/USDT:USDT": 1.0,
    # Стабильные альты с хорошей ликвидностью
    "SOL/USDT:USDT": 0.95,
    "XRP/USDT:USDT": 0.95,
    "BNB/USDT:USDT": 0.95,
    # Менее ликвидные — нужен более сильный сигнал
    "DOGE/USDT:USDT": 0.9,
    "ADA/USDT:USDT": 0.9,
    "AVAX/USDT:USDT": 0.85,
    "DOT/USDT:USDT": 0.85,
    "LINK/USDT:USDT": 0.85,
    "SUI/USDT:USDT": 0.8,
}


# ──────────────────────────────────────────────
#  AdaptiveThresholds — единая точка входа
# ──────────────────────────────────────────────

@dataclass
class ThresholdResult:
    """Результат вычисления порога."""
    value: float                # итоговый порог
    base: float                 # базовый порог
    vol_mult: float             # множитель волатильности
    session_mult: float         # множитель сессии
    symbol_mult: float          # множитель символа
    regime: str                 # режим волатильности
    session: str                # текущая сессия
    atr_pct: float              # ATR%


class AdaptiveThresholds:
    """
    Центральный калькулятор порогов.

    Использование в сигнале:
        threshold = adaptive.get("volume_z", symbol="BTC/USDT:USDT")
        if z_score < threshold.value: return None

    База + множители:
        итог = base × vol_mult × session_mult × symbol_mult
    """

    def __init__(self, volatility_tracker: VolatilityTracker):
        self.vol = volatility_tracker

    def get(
        self,
        threshold_name: str,
        symbol: str,
        base: float | None = None,
        override_vol_mult: float | None = None,
        override_session_mult: float | None = None,
    ) -> ThresholdResult:
        """
        Получить адаптивный порог.

        threshold_name: ключ порога (volume_z, spread_pct, rsi_period...)
        symbol: символ для калибровки
        base: базовое значение (если None — из registry)
        """
        # Базовое значение
        base_val = base if base is not None else _BASE_THRESHOLDS.get(threshold_name, 1.0)

        # Множитель волатильности
        if override_vol_mult is not None:
            vol_mult = override_vol_mult
        else:
            vol_mult = self.vol.get_multiplier(symbol)

        # Множитель сессии
        if override_session_mult is not None:
            session_mult = override_session_mult
        else:
            session = get_current_session()
            session_mult = SESSION_MULTIPLIERS.get(session, 1.0)

        # Множитель символа
        symbol_mult = SYMBOL_BASE_MULTIPLIERS.get(symbol, 0.85)

        # Итог
        final = base_val * vol_mult * session_mult * symbol_mult

        regime = self.vol.get_regime(symbol)
        atr_pct = self.vol.get_atr_pct(symbol)

        return ThresholdResult(
            value=final,
            base=base_val,
            vol_mult=vol_mult,
            session_mult=session_mult,
            symbol_mult=symbol_mult,
            regime=regime,
            session=get_current_session(),
            atr_pct=atr_pct,
        )


# ──────────────────────────────────────────────
#  Базовые пороги (registry)
# ──────────────────────────────────────────────

_BASE_THRESHOLDS: dict[str, float] = {
    # Volume
    "volume_z": 3.0,            # Z-score для объёма
    "volume_z_min": 2.0,        # минимальный (экстрим)
    "volume_z_max": 6.0,        # максимальный (штиль)
    "volume_multiplier": 1.5,   # множитель объёма к среднему

    # Orderbook
    "spread_pct": 0.001,        # 0.1% базовый спред
    "depth_ratio_high": 0.7,    # концентрация верхних 5 уровней
    "depth_ratio_low": 0.2,     # разреженность верхних 5 уровней
    "orderbook_imbalance": 3.0, # дисбаланс bid/ask

    # Candle
    "momentum_pct": 0.5,        # close-open > 0.5%
    "body_min_pct": 0.3,        # минимальный размер тела свечи
    "consecutive_count": 5,     # N свечей подряд
    "rsi_oversold": 30,         # RSI oversold
    "rsi_overbought": 70,       # RSI overbought

    # Liquidation
    "liq_volume_min": 5_000_000,  # мин объём ликвидаций
    "liq_ratio": 0.7,             # доля всех ликвидаций
    "liq_cascade": 5,             # N+ ликвидаций за 30с

    # Trade flow
    "buy_sell_ratio": 2.0,      # соотношение buy/sell
    "whale_min_notional": 100_000,  # мин размер кита

    # Whale accum
    "whale_accum_big": 250_000,
    "whale_accum_small": 50_000,

    # Wall
    "wall_threshold_ratio": 5.0,  # во сколько раз стена > среднего
    "min_walls": 2,
}


# ──────────────────────────────────────────────
#  Singleton
# ──────────────────────────────────────────────

_vol_tracker: VolatilityTracker | None = None
_adaptive: AdaptiveThresholds | None = None


def get_volatility_tracker() -> VolatilityTracker:
    global _vol_tracker
    if _vol_tracker is None:
        _vol_tracker = VolatilityTracker()
    return _vol_tracker


def get_adaptive_thresholds() -> AdaptiveThresholds:
    global _adaptive
    if _adaptive is None:
        _adaptive = AdaptiveThresholds(get_volatility_tracker())
    return _adaptive
