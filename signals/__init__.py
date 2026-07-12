"""
Signals — система обнаружения торговых паттернов.
Каждый сигнал расширяет BaseSignal и регистрируется через @register.
"""

from signals.base import (
    BaseSignal,
    SignalContext,
    SignalMeta,
    get_signal,
    get_signals_by_category,
    list_signals,
    register,
    _signal_registry,
)

# Авто-импорт сигналов для срабатывания @register
import signals.volume                # noqa: F401
import signals.whale                 # noqa: F401
import signals.smart_money           # noqa: F401
import signals.liquidation_signal    # noqa: F401
import signals.orderbook_signal      # noqa: F401
import signals.candle_technicals     # noqa: F401
import signals.candle_patterns       # noqa: F401
import signals.orderbook_signals     # noqa: F401
import signals.trade_flow            # noqa: F401
import signals.liquidation_advanced  # noqa: F401
import signals.hybrid                # noqa: F401
import signals.ai_score              # noqa: F401
import signals.correlation           # noqa: F401
import signals.relative_strength      # noqa: F401
import signals.sector                 # noqa: F401
import signals.breadth                # noqa: F401
import signals.heatmap                # noqa: F401
import signals.rotation               # noqa: F401
import signals.liquidity_zones        # noqa: F401
import signals.trend_strength         # noqa: F401
import signals.dna                    # noqa: F401
import signals.pattern_similarity     # noqa: F401
import signals.ai_clustering          # noqa: F401
import signals.lifecycle              # noqa: F401
import signals.market_analysis        # noqa: F401
import signals.replay                 # noqa: F401
import signals.explain                # noqa: F401
import signals.whale_v2               # noqa: F401 (FeatureEngine версия)
import signals.rsi_v2                 # noqa: F401 (RSI на FeatureEngine)
import signals.indicator_v2           # noqa: F401 (комбинированный на FeatureEngine)
import signals.consensus_v2           # noqa: F401 (консенсус на FeatureEngine)

__all__ = [
    "BaseSignal",
    "SignalContext",
    "SignalMeta",
    "get_signal",
    "get_signals_by_category",
    "list_signals",
    "register",
]
