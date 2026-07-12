"""Проверка фич в работающем процессе."""
import sys
sys.path.insert(0, "G:\\bot\\crypto-screener-v2")

import asyncio

async def main():
    from core.features import get_feature_engine
    
    fe = get_feature_engine()
    
    for sym in ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]:
        candles = await fe.get_feature(sym, "ohlcv.1m.buffer")
        trend = await fe.get_feature(sym, "regime.trend")
        vol = await fe.get_feature(sym, "vol.regime")
        
        print(f"{sym}: candles={len(candles) if candles else 0} trend={trend} vol={vol}")
        
        if candles and len(candles) >= 3:
            lc = float(candles[-1]["close"])
            pc = float(candles[-2]["close"])
            chg = (lc - pc) / pc * 100
            print(f"  change: {chg:.2f}%")
            
            last_dir = "buy" if float(candles[-1]["close"]) > float(candles[-1]["open"]) else "sell"
            count = 1
            for i in range(len(candles) - 2, -1, -1):
                c = candles[i]
                c_dir = "buy" if float(c["close"]) > float(c["open"]) else "sell"
                if c_dir == last_dir:
                    count += 1
                else:
                    break
            print(f"  consecutive: {count} ({last_dir})")

if __name__ == "__main__":
    asyncio.run(main())
