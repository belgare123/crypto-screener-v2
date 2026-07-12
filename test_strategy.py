"""Симуляция MomentumStrategy для отладки."""
import sys
sys.path.insert(0, "G:\\bot\\crypto-screener-v2")

import logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")

import asyncio

async def main():
    from core.features import get_feature_engine
    from context import ContextEngine
    import strategies.momentum_v2  # noqa: F401
    from strategies.base import StrategyContext
    from strategies import StrategyEngine

    fe = get_feature_engine()
    ce = ContextEngine(feature_engine=fe)

    se = StrategyEngine(feature_engine=fe, context_engine=ce, signal_engine=None)
    se.register_all()

    strat = se.get_strategy("momentum_v2")
    if not strat:
        print("Strategy not found")
        return

    print(f"Strategy: {strat.meta.name} min_score={strat.meta.min_score} cooldown={strat.meta.cooldown}")
    print()

    for sym in ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]:
        print(f"=== {sym} ===")
        ctx = await ce.get_context(sym)
        print(f"Context: trend={ctx.trend} vol={ctx.volatility} session={ctx.session_name}")

        sc = StrategyContext(symbol=sym, features=fe, context=ctx)
        result = await strat.evaluate(sc)
        if result:
            print(f"  >> RESULT: score={result.score:.0f} dir={result.direction} conf={result.confidence}")
            for f in result.factors:
                print(f"     factor: {f}")
        else:
            print("  >> NO RESULT")
        print()

if __name__ == "__main__":
    asyncio.run(main())
