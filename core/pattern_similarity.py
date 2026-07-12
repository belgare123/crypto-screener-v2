"""
Pattern Similarity — поиск исторически похожих сигналов.

Когда приходит Signal DNA, ищет в базе 5 самых похожих (по евклидову расстоянию)
и сообщает, что произошло после них.

Используется как надстройка над DNAStore.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from core import SignalResult
from core.signal_dna import SignalDNA, get_dna_store

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.85   # мин. коэффициент схожести (1.0 = идентично)
MAX_RESULTS = 5
HISTORY_LOOKBACK_HOURS = 48   # смотреть историю только за последние 48ч


@dataclass
class SimilarPattern:
    similarity: float = 0.0     # 0-1 (1 = идентично)
    original_id: int = 0
    original_timestamp: float = 0.0
    original_symbol: str = ""
    original_signal: str = ""
    original_score: float = 0.0
    original_direction: str = "neutral"
    price_at_signal: float = 0.0
    price_1h_later: float = 0.0
    price_change_1h: float = 0.0
    price_change_4h: float = 0.0
    outcome: str = "unknown"    # win / loss / unknown
    days_ago: float = 0.0


@dataclass
class PatternSimilaritySnapshot:
    timestamp: float = 0.0
    current_signal: str = ""
    current_signal_name: str = ""
    current_symbol: str = ""
    similar_patterns: list[SimilarPattern] = field(default_factory=list)
    avg_win_rate: float = 0.0
    avg_change_1h: float = 0.0
    signal_score: int = 0
    has_significant_pattern: bool = False
    strongest_pattern: str = "unknown"


class PatternSimilarityEngine:
    """
    Поиск похожих паттернов по DNA.
    """

    def __init__(self, dna_store=None, ticker_getter: Callable | None = None, candle_getter: Callable | None = None):
        self._store = dna_store or get_dna_store()
        self._ticker_getter = ticker_getter
        self._candle_getter = candle_getter
        self._last_snapshot: PatternSimilaritySnapshot | None = None

    @property
    def last_snapshot(self) -> PatternSimilaritySnapshot | None:
        return self._last_snapshot

    async def analyze(self, dna_vector: list[float], current_signal: SignalResult) -> PatternSimilaritySnapshot:
        """Поиск похожих паттернов для данного сигнала."""
        now = time.time()
        similar_dnas = self._store.search_similar(dna_vector, limit=MAX_RESULTS * 2)

        patterns = []
        for other in similar_dnas:
            # Пропускаем self (если current_signal уже сохранён в DNA)
            if other.signal_name == current_signal.signal_name and other.symbol == current_signal.symbol:
                continue
            if now - other.timestamp > HISTORY_LOOKBACK_HOURS * 3600:
                continue

            # Коэффициент схожести (1 / (1 + расстояние))
            dv = dna_vector
            ov = other.to_vector()
            if len(dv) != len(ov):
                continue
            dist = sum((a - b) ** 2 for a, b in zip(dv, ov)) ** 0.5
            similarity = 1.0 / (1.0 + dist)

            if similarity < SIMILARITY_THRESHOLD:
                continue

            # Что произошло после сигнала?
            change_1h = await self._get_price_change(other, hours=1)
            change_4h = await self._get_price_change(other, hours=4)

            # Outcome
            if change_1h is not None:
                if other.direction == "buy" and change_1h > 0:
                    outcome = "win"
                elif other.direction == "sell" and change_1h < 0:
                    outcome = "win"
                elif abs(change_1h) < 0.001:
                    outcome = "flat"
                else:
                    outcome = "loss"
            else:
                outcome = "unknown"

            patterns.append(SimilarPattern(
                similarity=round(similarity, 3),
                original_id=other.id,
                original_timestamp=other.timestamp,
                original_symbol=other.symbol,
                original_signal=other.signal_name,
                original_score=other.score,
                original_direction=other.direction,
                price_at_signal=other.price,
                price_change_1h=change_1h or 0.0,
                price_change_4h=change_4h or 0.0,
                outcome=outcome,
                days_ago=round((now - other.timestamp) / 86400, 1),
            ))

        patterns.sort(key=lambda p: p.similarity, reverse=True)
        patterns = patterns[:MAX_RESULTS]

        wins = [p for p in patterns if p.outcome == "win"]
        avg_win_rate = len(wins) / max(len(patterns), 1) * 100
        avg_change_1h = sum(p.price_change_1h for p in patterns) / max(len(patterns), 1)

        # Определяем самый сильный паттерн
        strongest = "unknown"
        if patterns:
            best = max(patterns, key=lambda p: p.similarity)
            strongest = f"{best.original_symbol} {best.original_signal} ({best.similarity*100:.0f}%)"

        snap = PatternSimilaritySnapshot(
            timestamp=time.time(),
            current_signal=current_signal.symbol,
            current_signal_name=current_signal.signal_name,
            current_symbol=current_signal.symbol,
            similar_patterns=patterns,
            avg_win_rate=avg_win_rate,
            avg_change_1h=avg_change_1h,
            signal_score=current_signal.score,
            has_significant_pattern=len(patterns) >= 2,
            strongest_pattern=strongest,
        )

        self._last_snapshot = snap
        return snap

    async def _get_price_change(self, dna: SignalDNA, hours: int = 1) -> float | None:
        """Получить изменение цены через N часов после сигнала."""
        if not self._candle_getter or not dna.price or dna.price == 0:
            return None

        symbol = dna.symbol
        timeframe = "5" if hours == 1 else "15"
        periods = int(hours * 60 / int(timeframe)) + 5

        candles = self._candle_getter(symbol, timeframe, periods)
        if not candles or len(candles) < periods:
            return None

        last_close = float(candles[-1].get("close", 0))
        if not last_close:
            return None

        return (last_close - dna.price) / dna.price * 100

    def to_signal(self) -> SignalResult | None:
        """Сигнал при обнаружении похожего паттерна с win_rate > 70%."""
        snap = self._last_snapshot
        if not snap or not snap.has_significant_pattern:
            return None

        if snap.avg_win_rate < 60:
            return None

        score = min(snap.avg_win_rate + 10, 95)

        # Детали самых похожих
        top = snap.similar_patterns[:3] if snap.similar_patterns else []
        meta = {
            "current_signal": snap.current_signal_name,
            "symbol": snap.current_symbol,
            "similar_count": len(snap.similar_patterns),
            "avg_win_rate": round(snap.avg_win_rate, 0),
            "avg_change_1h": round(snap.avg_change_1h, 2),
            "strongest_pattern": snap.strongest_pattern,
            "patterns": [
                {
                    "symbol": p.original_symbol,
                    "similarity": p.similarity,
                    "outcome": p.outcome,
                    "change_1h": round(p.price_change_1h, 2),
                    "days_ago": p.days_ago,
                }
                for p in top
            ],
            "description": f"Паттерн похож на {len(snap.similar_patterns)} исторических (win rate {snap.avg_win_rate:.0f}%)",
        }

        direction = "buy" if snap.avg_change_1h > 0 else ("sell" if snap.avg_change_1h < 0 else "neutral")

        return SignalResult(
            signal_name="pattern_similarity",
            symbol=snap.current_symbol,
            exchange="bybit",
            score=round(score, 0),
            direction=direction,
            meta=meta,
            ts=snap.timestamp,
            cooldown=7200,
        )


_pattern_sim_engine: PatternSimilarityEngine | None = None


def get_pattern_similarity_engine(ticker_getter=None, candle_getter=None) -> PatternSimilarityEngine:
    global _pattern_sim_engine
    if _pattern_sim_engine is None:
        _pattern_sim_engine = PatternSimilarityEngine(ticker_getter=ticker_getter, candle_getter=candle_getter)
    return _pattern_sim_engine
