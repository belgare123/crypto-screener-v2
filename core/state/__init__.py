"""State Engine — единая модель состояния рынка.

MarketState объединяет все измерения:
- trend: направление + сила тренда
- volatility: режим волатильности
- liquidity: уровень ликвидности  
- noise: шум/трендовость рынка
- participation: тип участников
"""

from __future__ import annotations

from .market_state import MarketStateSnapshot
from .engine import StateEngine, get_state_engine

__all__ = [
    "MarketStateSnapshot",
    "StateEngine",
    "get_state_engine",
]
