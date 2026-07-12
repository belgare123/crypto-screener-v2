"""
Correlation Engine — отслеживает взаимосвязи между активами.

Задачи:
  1. Rolling correlation BTC ↔ ETH ↔ SOL ↔ алты
  2. Лидеры рынка (кто двинулся первым)
  3. Дивергенции (рассинхронизация)

Данные:
  - Потребляет ticker-события из шины (или опрашивает ticker_store)
  - Хранит rolling window цен для каждого символа
  - Выдаёт метрики для CorrelationSignal

Как работает:
  Каждые N секунд CorrelationEngine сэмплирует цены из ticker_store
  и обновляет rolling windows. Затем считает корреляцию Пирсона
  между парами за заданный период.
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  Типы корреляции
# ──────────────────────────────────────────────

class CorrelationType(str, Enum):
    HIGH = "high"       # > 0.8
    MODERATE = "moderate"  # 0.5-0.8
    LOW = "low"         # 0.2-0.5
    DECOUPLED = "decoupled"  # < 0.2
    NEGATIVE = "negative"    # < -0.2


def classify_correlation(r: float) -> CorrelationType:
    r = abs(r)
    if r > 0.8:
        return CorrelationType.HIGH
    elif r > 0.5:
        return CorrelationType.MODERATE
    elif r > 0.2:
        return CorrelationType.LOW
    else:
        return CorrelationType.DECOUPLED


# ──────────────────────────────────────────────
#  Иерархия рынка (для определения лидера)
# ──────────────────────────────────────────────

# Порядок: от ликвидного ядра к алтам
TIER_1 = ["BTC/USDT:USDT", "ETH/USDT:USDT"]  # ядро
TIER_2 = ["SOL/USDT:USDT", "XRP/USDT:USDT", "BNB/USDT:USDT"]  # крупные альты
TIER_3 = ["DOGE/USDT:USDT", "ADA/USDT:USDT", "AVAX/USDT:USDT", "DOT/USDT:USDT", "LINK/USDT:USDT", "SUI/USDT:USDT"]  # средние

# Маппинг: символ → его порядковый номер в иерархии (меньше = ближе к ядру)
HIERARCHY: dict[str, int] = {}
for i, sym in enumerate(TIER_1 + TIER_2 + TIER_3):
    HIERARCHY[sym] = i


# ──────────────────────────────────────────────
#  Rolling price window
# ──────────────────────────────────────────────

@dataclass
class PriceSeries:
    """Дешёвый rolling window цен с возвратами."""
    maxlen: int = 120  # 120 точек = 10 минут на 5-сек интервалах
    _prices: list[float] = field(default_factory=list)
    _timestamps: list[float] = field(default_factory=list)

    def add(self, price: float, ts: float | None = None):
        self._prices.append(price)
        self._timestamps.append(ts or time.time())
        if len(self._prices) > self.maxlen:
            self._prices.pop(0)
            self._timestamps.pop(0)

    @property
    def length(self) -> int:
        return len(self._prices)

    def return_since(self, bars_ago: int) -> float | None:
        """Доходность за N свечей (pct_change)."""
        if self.length < bars_ago + 1:
            return None
        prev = self._prices[-(bars_ago + 1)]
        cur = self._prices[-1]
        if prev == 0:
            return None
        return (cur - prev) / prev * 100

    def last(self, n: int) -> list[float]:
        """Последние N цен."""
        return self._prices[-n:] if self.length >= n else list(self._prices)

    def returns(self, n: int) -> list[float]:
        """Последовательные доходности за последние N цен."""
        if self.length < n:
            return []
        prices = self._prices[-n:]
        rets = []
        for i in range(1, len(prices)):
            if prices[i - 1] != 0:
                rets.append((prices[i] - prices[i - 1]) / prices[i - 1])
        return rets

    def correlation(self, other: PriceSeries, bars: int = 20) -> float | None:
        """Корреляция Пирсона с другим PriceSeries за последние bars цен."""
        a = self.last(bars)
        b = other.last(bars)
        if len(a) < 3 or len(b) < 3 or len(a) != len(b):
            return None
        n = len(a)
        mean_a = sum(a) / n
        mean_b = sum(b) / n
        cov = sum((ai - mean_a) * (bi - mean_b) for ai, bi in zip(a, b))
        var_a = sum((ai - mean_a) ** 2 for ai in a)
        var_b = sum((bi - mean_b) ** 2 for bi in b)
        if var_a == 0 or var_b == 0:
            # В штиль все цены равны — корреляция 1.0 (тривиально)
            return 1.0
        r = cov / (math.sqrt(var_a) * math.sqrt(var_b))
        return max(-1.0, min(1.0, r))

    def z_score(self) -> float | None:
        """Z-score текущей цены относительно rolling среднего."""
        if self.length < 10:
            return None
        prices = self._prices
        mean_p = sum(prices) / len(prices)
        var_p = sum((p - mean_p) ** 2 for p in prices) / len(prices)
        if var_p == 0:
            return None
        return (prices[-1] - mean_p) / math.sqrt(var_p)


# ──────────────────────────────────────────────
#  CorrelationSnapshot — полное состояние
# ──────────────────────────────────────────────

@dataclass
class CorrelationSnapshot:
    timestamp: float = 0.0
    pairs: dict[tuple[str, str], float] = field(default_factory=dict)      # (a, b) → r
    leaders: list[str] = field(default_factory=list)        # символы, двинувшиеся раньше всех
    laggards: list[str] = field(default_factory=list)       # символы, двинувшиеся позже всех
    z_scores: dict[str, float] = field(default_factory=dict)  # символ → z
    divergences: list[str] = field(default_factory=list)    # символы с дивергенцией
    alignment: float = 0.5  # 0=полный хаос, 1=всё скоррелировано (дефолт нейтрал)


# ──────────────────────────────────────────────
#  CorrelationEngine
# ──────────────────────────────────────────────

class CorrelationEngine:
    """
    Отслеживает корреляции между символами.

    Использование:
        engine = CorrelationEngine()
        await engine.tick(current_prices)   # раз в 5-10 секунд
        snap = engine.get_snapshot()

    Для сигналов:
        corr = engine.pair_correlation("BTC/USDT:USDT", "SOL/USDT:USDT")
    """

    SAMPLE_INTERVAL = 2   # секунд между сэмплами
    CORR_BARS = 8         # сколько баров для корреляции (= 16с при SAMPLE_INTERVAL=2)

    TRACKED_SYMBOLS = TIER_1 + TIER_2 + TIER_3
    CORE_SYMBOLS = set(TIER_1)
    ALTS = set(TIER_2 + TIER_3)

    def __init__(self):
        self._series: dict[str, PriceSeries] = defaultdict(PriceSeries)
        self._last_snapshot = CorrelationSnapshot()
        self._initialized = False

    # ── API ──

    def record_price(self, symbol: str, price: float):
        """Добавить точку цены (вызывается из тикера)."""
        if symbol not in self.TRACKED_SYMBOLS:
            return
        self._series[symbol].add(price)
        self._initialized = True

    def pair_correlation(self, sym_a: str, sym_b: str, bars: int | None = None) -> float | None:
        """Корреляция Пирсона между двумя символами."""
        if sym_a not in self._series or sym_b not in self._series:
            return None
        return self._series[sym_a].correlation(self._series[sym_b], bars or self.CORR_BARS)

    def get_snapshot(self) -> CorrelationSnapshot:
        return self._last_snapshot

    def get_alignment(self) -> float:
        """Общая согласованность рынка (0-1). 1 = всё коррелирует."""
        return self._last_snapshot.alignment

    def get_leaders(self) -> list[str]:
        """Символы, которые ведут рынок."""
        return self._last_snapshot.leaders

    # ── Основной тик ──

    async def tick(self, prices: dict[str, float] | None = None):
        """
        Обновить корреляции.

        Если prices не передан — пропускает (ждём данные от тикера).
        """
        for symbol, price in (prices or {}).items():
            self.record_price(symbol, price)

        if not self._initialized:
            return

        snap = CorrelationSnapshot(timestamp=time.time())

        # 1. Попарные корреляции
        symbols_with_data = [s for s in self.TRACKED_SYMBOLS if s in self._series and self._series[s].length > 5]
        for i, a in enumerate(symbols_with_data):
            for b in symbols_with_data[i + 1:]:
                r = self._series[a].correlation(self._series[b], self.CORR_BARS)
                if r is not None:
                    snap.pairs[(a, b)] = r

        if not snap.pairs:
            self._last_snapshot = snap
            return

        # 2. Z-scores
        for s in symbols_with_data:
            z = self._series[s].z_score()
            if z is not None:
                snap.z_scores[s] = z

        # 3. Лидеры и догоняющие
        #   Смотрим последние 3 возврата каждой пары.
        #   Если SOL дал +0.5% раньше чем ETH дал +0.3% — SOL лидер
        leaders = []
        laggards = []

        # Простая эвристика: кто двинулся сильнее за последнее время — тот лидер
        returns_map: dict[str, float] = {}
        for s in symbols_with_data:
            ret = self._series[s].return_since(3)  # ~15 секунд
            if ret is not None:
                returns_map[s] = ret

        if returns_map:
            sorted_by_return = sorted(returns_map.items(), key=lambda x: -abs(x[1]))
            if sorted_by_return:
                # Лидеры — топ-3 по абсолютному движению
                leaders = [s for s, _ in sorted_by_return[:3]]
                # Отстающие — кто двинулся меньше всех
                laggards = [s for s, _ in sorted_by_return[-3:]]

        snap.leaders = leaders[:3]
        snap.laggards = laggards[:3]

        # 4. Дивергенции: symbol с z-score > 2.0 при общем спокойствии
        divergence_threshold = 2.0
        if snap.z_scores and snap.pairs:
            # Средняя корреляция
            avg_corr = sum(snap.pairs.values()) / len(snap.pairs)
            for s, z in snap.z_scores.items():
                if abs(z) > divergence_threshold and avg_corr < 0.5:
                    # Движется сильно, но рынок не скоррелирован
                    snap.divergences.append(s)

        # 5. Alignment: насколько рынок единый
        #    Ближе к 1 = высокая средняя корреляция
        if snap.pairs:
            avg_r = sum(snap.pairs.values()) / len(snap.pairs)
            snap.alignment = max(0, min(1, (avg_r + 1) / 2))  # -1..1 → 0..1

        self._last_snapshot = snap

    def get_info(self) -> dict[str, Any]:
        """Полный отчёт для лога / сигнала."""
        snap = self._last_snapshot
        pairs_summary = {}
        for (a, b), r in snap.pairs.items():
            short_a = a.split("/")[0]
            short_b = b.split("/")[0]
            pairs_summary[f"{short_a}_{short_b}"] = round(r, 2)

        return {
            "alignment": round(snap.alignment, 2),
            "leaders": [s.split("/")[0] for s in snap.leaders],
            "laggards": [s.split("/")[0] for s in snap.laggards],
            "divergences": [s.split("/")[0] for s in snap.divergences],
            "pairs": pairs_summary,
            "tracked_count": len(self._series),
        }


# ──────────────────────────────────────────────
#  Singleton
# ──────────────────────────────────────────────

_engine: CorrelationEngine | None = None


def get_correlation_engine() -> CorrelationEngine:
    global _engine
    if _engine is None:
        _engine = CorrelationEngine()
    return _engine
