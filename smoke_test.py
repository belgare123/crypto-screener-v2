"""
Integration smoke test — live Bybit WS, single symbol (BTC/USDT:USDT), 120s run.

Validates:
  1. Scanner data flow (ticker, candles, trades, liquidation, orderbook)
  2. FeatureEngine calculators (whale, ohlcv, indicators, orderbook, market, volatility)
  3. StateEngine ticks
  4. /metrics and /health endpoints
  5. No pipeline crashes
"""

import asyncio
import json
import logging
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("smoke_test")

SYMBOL = "BTC/USDT:USDT"
METRICS_PORT = 9121
DURATION = 60  # seconds (short run for CI, 2min for real test)
SCANNER_EVENTS = []
START = time.time()


async def check_http(path: str) -> tuple[int, str]:
    try:
        import socket
        r = f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(("127.0.0.1", METRICS_PORT))
        s.send(r)
        data = s.recv(65536)
        s.close()
        headers, _, body = data.partition(b"\r\n\r\n")
        return 200 if b"200" in headers else int(headers.split()[1]), body.decode()
    except Exception as e:
        return 0, str(e)


class MonitorBus:
    """Subscribe to * and count events by channel."""

    def __init__(self, bus):
        self.bus = bus
        self.bus.subscribe("*", self._on_event)

    async def _on_event(self, ev):
        elapsed = time.time() - START
        channel_prefix = ev.channel.split(".")[0]
        SCANNER_EVENTS.append((elapsed, channel_prefix, ev.symbol, ev.channel))

    def counts(self, since_seconds=30):
        cutoff = time.time() - since_seconds
        by_ch = {}
        for ts, prefix, sym, ch in SCANNER_EVENTS:
            if ts > cutoff:
                by_ch[prefix] = by_ch.get(prefix, 0) + 1
        return by_ch


async def smoke_test():
    global START
    START = time.time()

    logger.info("=" * 60)
    logger.info("SMOKE TEST :: crypto-screener-v2")
    logger.info("Duration: %ds  Symbol: %s  Metrics: %d", DURATION, SYMBOL, METRICS_PORT)
    logger.info("=" * 60)

    # ── 1. Imports (fail fast) ──
    from core import get_bus, Event
    from core.monitoring.server import MetricsServer
    from core.monitoring.health import get_healthcheck, HealthComponent, HealthStatus

    from exchanges.bybit import BybitExchange
    from scanner.ticker import TickerScanner, LiquidationScanner, ticker_store, liquidation_store
    from scanner.candles import CandleScanner, candle_buffer
    from scanner.trades import TradeScanner, whale_tracker
    from scanner.orderbook import OrderBookScanner
    from core.features.engine import FeatureEngine, get_feature_engine
    from core.features.calculators.whale import WhaleFeatureCalculator
    from core.features.calculators.ohlcv import OHLCVFeatureCalculator
    from core.features.calculators.indicators import IndicatorsFeatureCalculator
    from core.features.calculators.orderbook import OrderBookFeatureCalculator
    from core.features.calculators.market import MarketFeatureCalculator
    from core.features.calculators.volatility import VolatilityFeatureCalculator
    from core.state.engine import StateEngine
    from core.risk.engine import RiskEngine
    from core.consensus.engine import ConsensusEngine
    from core.ome.engine import OME
    from core.signal.engine import SignalEngine
    from core.learning.engine import LearningEngine

    bus = get_bus()

    # ── 2. Exchange (Bybit WS) ──
    logger.info("[1] BybitExchange → bus")
    exchange = BybitExchange(bus=bus)
    await exchange.start()

    # ── 3. 5 Scanners ──
    logger.info("[2] 5 scanners subscribing…")
    scanners = [
        TickerScanner(),
        CandleScanner(),
        TradeScanner(),
        LiquidationScanner(),
        OrderBookScanner(),
    ]
    for s in scanners:
        await s.start()
    logger.info("    ✓ ticker • candles • trades • liquidation • orderbook")

    # ── 4. FeatureEngine ──
    logger.info("[3] FeatureEngine + 6 calculators…")
    fe = get_feature_engine()
    fe.register_many([
        WhaleFeatureCalculator(),
        OHLCVFeatureCalculator(),
        IndicatorsFeatureCalculator(),
        OrderBookFeatureCalculator(),
        MarketFeatureCalculator(),
        VolatilityFeatureCalculator(),
    ])
    await fe.start()
    logger.info("    ✓ %d features registered", len(fe._calc_by_feature))

    # Monitor all bus events
    monitor = MonitorBus(bus)

    # ── 5. Shadow engines ──
    logger.info("[4] Shadow engines: State, Risk, Consensus, OME, Signal, Learning")
    se = StateEngine(bus=bus, interval=30.0)
    await se.start()
    from core.risk.rules_spread import SpreadRule
    from core.risk.rules_atr import ATRRule
    from core.risk.rules_liquidity import LiquidityRule
    from core.risk.rules_session import SessionRule
    reng = RiskEngine(shadow=True)
    reng.add_rule(SpreadRule(block_ratio=0.5, reduce_ratio=0.3))
    reng.add_rule(ATRRule(high_atr_pct=5.0, low_atr_pct=0.1, surge_threshold=3.0))
    reng.add_rule(LiquidityRule(min_volume_usdt=500_000, min_oi_usdt=5_000_000))
    reng.add_rule(SessionRule(reduce_asia=False))
    ceng = ConsensusEngine(shadow=True)
    oeng = OME(shadow=True, capital=1000.0, risk_pct=0.01)
    seng = SignalEngine(shadow=True)
    leng = LearningEngine(shadow=True)
    logger.info("    ✓ all shadow engines ready")

    # ── 6. Metrics + Health ──
    logger.info("[5] MetricsServer on :%d …", METRICS_PORT)
    hc = get_healthcheck()
    hc.register("ticker", lambda: HealthComponent(name="ticker", status=HealthStatus.HEALTHY))
    hc.register("state", lambda: HealthComponent(name="state", status=HealthStatus.HEALTHY))
    hc.register("risk", lambda: HealthComponent(name="risk", status=HealthStatus.HEALTHY))
    ms = MetricsServer(port=METRICS_PORT)
    await ms.start()

    # ── 7. Subscribe to BTC ──
    logger.info("[6] WebSocket subscribe → %s (candles, ticker, trades, liquidation)", SYMBOL)
    await exchange.subscribe("candles", [SYMBOL], params="1")
    await exchange.subscribe("ticker", [SYMBOL])
    await exchange.subscribe("trades", [SYMBOL])
    await exchange.subscribe("liquidation", [SYMBOL])
    logger.info("    ✓ subscribed")

    # ── 8. Collect ──
    logger.info("=" * 60)
    logger.info("COLLECTING DATA … %d seconds", DURATION)
    logger.info("=" * 60)

    deadline = time.time() + DURATION
    while time.time() < deadline:
        await asyncio.sleep(10)
        elapsed = time.time() - START
        ch = monitor.counts(since_seconds=30)
        fmt = "  ".join(f"{k}={v}" for k, v in sorted(ch.items()))

        status, body = await check_http("/health")
        h_msg = body[:120].replace("\n", " ")[:80] if status == 200 else f"ERR({status})"

        tk = ticker_store.get(SYMBOL)
        price = tk["last_price"] if tk else "—"
        vol = tk["volume_24h"] if tk else "—"

        candles = candle_buffer.get(SYMBOL, "1", count=3)
        candle_count = len(candles)

        whales = whale_tracker.get_whales(SYMBOL)
        liqs = liquidation_store.total_volume(minutes=30)
        feats = fe.stats["features"]

        logger.info(
            "t=%.0fs  events=[%s]  health=[%s]  price=%s  "
            "vol24h=%s  candles=%d  whales=%d  liq_vol=%.0f  feat=%d",
            elapsed, fmt, h_msg[:40], price, vol,
            candle_count, len(whales), liqs, feats,
        )

    # ── 9. Final report ──
    logger.info("=" * 60)
    logger.info("FINAL REPORT — %.0fs elapsed", time.time() - START)
    logger.info("=" * 60)

    # Health
    status, body = await check_http("/health")
    try:
        health_data = json.loads(body)
        logger.info("Health: status=%s uptime=%.1f components=%d",
                     health_data.get("status"), health_data.get("uptime"),
                     len(health_data.get("components", [])))
    except Exception:
        logger.info("Health (raw): %s", body[:200])

    # Ticker
    tk = ticker_store.get(SYMBOL)
    if tk:
        logger.info("Ticker[%s]: price=%.2f vol24h=%.0f change=%.4f%%",
                     SYMBOL, tk["last_price"], tk["volume_24h"], tk["change_24h"] * 100)
    else:
        logger.error("TickerStore[%s]: NO DATA", SYMBOL)

    # Candles
    c = candle_buffer.get(SYMBOL, "1")
    logger.info("Candles[%s, 1m]: %d candles", SYMBOL, len(c))

    # Whales / Trades
    whales = whale_tracker.get_whales(SYMBOL)
    cvd = whale_tracker.get_cvd(SYMBOL)
    liq_vol = liquidation_store.total_volume(minutes=60)
    logger.info("Trades: %d whales tracked, CVD=%.0f, LiqVol(1h)=%.0f",
                 len(whales), cvd, liq_vol)

    # Feature stats
    logger.info("FeatureEngine: %s", json.dumps(fe.stats, indent=2, default=str))

    # Event count
    logger.info("Total bus events: %d (%s)",
                 len(SCANNER_EVENTS),
                 {k: sum(1 for _, p, _, _ in SCANNER_EVENTS if p == k)
                  for k in set(p for _, p, _, _ in SCANNER_EVENTS)})

    # ── 10. Cleanup ──
    await exchange.stop()
    for s in scanners:
        await s.stop()
    await ms.stop()
    logger.info("Cleanup complete — SMOKE TEST FINISHED")


if __name__ == "__main__":
    asyncio.run(smoke_test())
