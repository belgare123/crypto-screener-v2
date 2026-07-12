"""
bootstrap.py — управляемый жизненный цикл Crypto Screener v2.

Заменяет монолитный run.py (818 строк) на фазовую инициализацию
через Container с явными зависимостями и graceful shutdown.

Использование:
    python bootstrap.py

Фазы:
    1. INFRASTRUCTURE — логирование, сигналы ОС
    2. EXCHANGE — BybitExchange, MarketDataBus
    3. STORAGE — CandleStore, TickerStore, OBStore, TradeStore, LiquidationStore, WhaleTracker
    4. FEATURES — FeatureEngine + 6 calculators
    5. BATCH_UPDATER — BatchFeatureUpdater (background batch refresh)
    6. STATE — StateEngine (regime, trend, volatility)
    7. CONTEXT — ContextEngine (L3)
    8. DECISION — DecisionEngine (adaptive thresholds + SL/TP)
    9. STRATEGY — StrategyEngine (L4)
    10. SERVICES — фоновые тикеры (correlation, heatmap, breadth, rotation, etc.)
    11. TELEGRAM — TelegramNotifier, handlers, polling
    12. WARMUP — FeatureEngine warmup (REST)
    13. RUN — подписка на каналы, ожидание shutdown
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time

from core.di import Container, Phase

logger = logging.getLogger(__name__)

# ── Константы ──
TOP_SYMBOLS = [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
    "XRP/USDT:USDT", "DOGE/USDT:USDT", "ADA/USDT:USDT",
    "AVAX/USDT:USDT", "DOT/USDT:USDT", "LINK/USDT:USDT",
    "SUI/USDT:USDT",
]
OB_SYMBOLS = ["BTC/USDT:USDT", "ETH/USDT:USDT"]
CHANNELS = ["candles", "trades", "ticker", "liquidation"]
EXTRA_TFS = ["5", "15"]
SNAPSHOT_INTERVAL = 300  # сек


# ══════════════════════════════════════════════
#  Фазы инициализации
# ══════════════════════════════════════════════


def phase_infrastructure(c: Container):
    """Phase 1: Логирование, обработчики сигналов ОС."""
    import logging as _logging

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _logging.getLogger("websockets").setLevel(_logging.WARNING)
    _logging.getLogger("asyncio").setLevel(_logging.WARNING)

    c.loop_start_time = asyncio.get_event_loop().time()
    logger.info("=" * 60)
    logger.info("Crypto Screener v2 starting...")
    logger.info("=" * 60)


def phase_exchange(c: Container):
    """Phase 2: Exchange + Data Bus."""
    from exchanges.bybit import BybitExchange
    from core import get_bus

    exchange = BybitExchange()
    bus = get_bus()

    c.set("exchange", exchange)
    c.set("bus", bus)
    logger.info("[exchange] BybitExchange created")


def phase_storage(c: Container):
    """Phase 3: Storage layer."""
    from core.storage import (
        get_candle_store, get_ticker_store, get_ob_store,
        get_liquidation_store, get_whale_tracker,
    )

    c.set("candle_store", get_candle_store())
    c.set("ticker_store", get_ticker_store())
    c.set("ob_store", get_ob_store())
    c.set("liquidation_store", get_liquidation_store())
    c.set("whale_tracker", get_whale_tracker())

    logger.info("[storage] Stores initialized")


def phase_features(c: Container):
    """Phase 4: FeatureEngine + calculators."""
    from core.features import get_feature_engine
    from core.features.calculators.whale import WhaleFeatureCalculator
    from core.features.calculators.ohlcv import OHLCVFeatureCalculator
    from core.features.calculators.indicators import IndicatorsFeatureCalculator
    from core.features.calculators.orderbook import OrderBookFeatureCalculator
    from core.features.calculators.market import MarketFeatureCalculator
    from core.features.calculators.volatility import VolatilityFeatureCalculator

    try:
        fe = get_feature_engine()
        fe.register(WhaleFeatureCalculator())
        fe.register(OHLCVFeatureCalculator())
        fe.register(IndicatorsFeatureCalculator())
        fe.register(OrderBookFeatureCalculator())
        fe.register(MarketFeatureCalculator())
        fe.register(VolatilityFeatureCalculator())

        n_calcs = len(fe._calculators)
        n_feats = sum(len(calc.feature_names) for calc in fe._calculators)
        logger.info("[features] FeatureEngine ready: %d calculators, %d features", n_calcs, n_feats)

        c.set("feature_engine", fe)
    except Exception:
        logger.exception("[features] Failed to init FeatureEngine — continuing without it")
        c.set("feature_engine", None)


def phase_batch_updater(c: Container):
    """Phase 5: BatchFeatureUpdater — background batch refresh of features."""
    fe = c.get("feature_engine")
    if fe is None:
        logger.info("[batch] No FeatureEngine — skipping BatchFeatureUpdater")
        c.set("batch_updater", None)
        return

    from core.features.batch_updater import BatchFeatureUpdater

    updater = BatchFeatureUpdater(feature_engine=fe, interval=5.0)
    c.set("batch_updater", updater)
    logger.info("[batch] BatchFeatureUpdater created (interval=5.0s)")


def phase_state(c: Container):
    """Phase 5: StateEngine."""
    from core.state import get_state_engine

    se = get_state_engine()
    logger.info("[state] StateEngine ready (shadow=%s, interval=%.0fs)", se.shadow, se.interval)
    c.set("state_engine", se)


async def phase_context(c: Container):
    """Phase 6: Reactive ContextEngine (L3)."""
    from context import ContextEngine

    ce = ContextEngine(
        feature_store=c.get("feature_store"),
        session_engine=c.get("session_engine"),
        bus=c.get("bus"),
        default_ttl=30.0,
    )
    c.set("context_engine", ce)
    await ce.start()
    logger.info("[context] ContextEngine ready (TTL=30s)")


def phase_decision(c: Container):
    """Phase 7: DecisionEngine (adaptive thresholds + SL/TP)."""
    from core.decision import DecisionEngine

    ce = DecisionEngine(
        context_engine=c.get("context_engine"),
        consensus_engine=c.get("consensus_engine"),
        feature_store=c.get("feature_store"),
        default_threshold=60.0,
    )
    if c.get("consensus_engine") is None:
        logger.info("[decision] no consensus_engine yet — will be set in phase_services")
    c.set("decision_engine", ce)
    logger.info("[decision] DecisionEngine ready (shadow=%s)", ce.shadow)


def phase_strategy(c: Container):
    """Phase 8: StrategyEngine — sends directly to TelegramNotifier."""
    from context import ContextEngine
    from strategies import StrategyEngine

    # Получаем или создаём ContextEngine
    ce = c.get("context_engine")
    if ce is None:
        from context import ContextEngine

        ce = ContextEngine(
            feature_store=c.get("feature_store"),
            bus=c.get("bus"),
            default_ttl=30.0,
        )
        c.set("context_engine", ce)

    # Импорт стратегий триггерит @register_strategy
    import strategies.momentum_v2  # noqa: F401

    se = StrategyEngine(
        feature_engine=c.get("feature_engine"),
        context_engine=ce,
        notifier=c.get("notifier"),
        signal_engine=None,  # V1 SignalEngine removed in v0.10.0
    )
    se.register_all()
    logger.info("[strategy] StrategyEngine ready (%d strategies)", len(se._strategies))
    from strategies import set_strategy_engine as _set_se
    _set_se(se)
    c.set("strategy_engine", se)


def phase_services(c: Container):
    """Phase 9: Фоновые сервисы (на основе core/* одноразовые engine'ы и таскер-циклы)."""
    from core.correlation import CorrelationEngine
    from core.rotation import RotationDetector
    from core.relative_strength import RelativeStrengthEngine
    from core.sector_scanner import SectorScannerEngine as SectorEngine
    from core.heatmap import HeatmapEngine
    from core.liquidity_zones import LiquidityZoneEngine
    from core.market_breadth import MarketBreadthEngine
    from core.trend_strength import TrendStrengthEngine
    from core.session import SessionEngine
    from core.signal_dna import DNAStore
    from core.signal_lifecycle import SignalLifecycleEngine
    from core.market_replay import MarketReplayEngine
    from core.market_analysis import ExpectedMoveEngine as MarketAnalysisEngine
    from core.pattern_similarity import PatternSimilarityEngine
    from core.ai_clustering import ClusteringEngine
    from core.exchanges import DataEngine

    ticker_store = c.require("ticker_store")
    candle_store = c.require("candle_store")
    ob_store = c.require("ob_store")
    bus = c.require("bus")

    def _ticker_all_getter():
        return ticker_store.all_sync()

    def _candle_getter(symbol: str, tf: str = "5m", limit: int = 100):
        return candle_store.get_sync(symbol, tf, limit)

    def _ob_getter(symbol: str):
        return ob_store.get_sync(symbol)

    # Session
    session_engine = SessionEngine()
    c.set("session_engine", session_engine)

    # Correlation
    corr_engine = CorrelationEngine()
    c.set("correlation_engine", corr_engine)

    # Relative Strength
    rs_engine = RelativeStrengthEngine(candle_store)
    c.set("rs_engine", rs_engine)

    # Sector
    sector_engine = SectorEngine()
    c.set("sector_engine", sector_engine)

    # Rotation
    rotation_detector = RotationDetector(sector_engine=sector_engine)
    c.set("rotation_detector", rotation_detector)

    # Heatmap
    heatmap_engine = HeatmapEngine(ticker_getter=_ticker_all_getter)
    c.set("heatmap_engine", heatmap_engine)

    # Liquidity Zones
    liquidity_engine = LiquidityZoneEngine(candle_getter=_candle_getter, ob_getter=_ob_getter)
    c.set("liquidity_engine", liquidity_engine)

    # Breadth
    breadth_engine = MarketBreadthEngine(ticker_getter=_ticker_all_getter)
    c.set("breadth_engine", breadth_engine)

    # Trend Strength
    trend_engine = TrendStrengthEngine(candle_getter=_candle_getter)
    c.set("trend_engine", trend_engine)

    # DNA Store + Lifecycle
    dna_store = DNAStore()
    lifecycle_engine = SignalLifecycleEngine()
    c.set("dna_store", dna_store)
    c.set("lifecycle_engine", lifecycle_engine)

    # Replay
    replay_engine = MarketReplayEngine()
    c.set("replay_engine", replay_engine)

    # Market Analysis
    analysis_engine = MarketAnalysisEngine()
    c.set("analysis_engine", analysis_engine)

    # AI Clustering
    clustering = ClusteringEngine(dna_store)
    c.set("clustering_engine", clustering)

    # Pattern Similarity
    pattern_sim = PatternSimilarityEngine(dna_store)
    c.set("pattern_sim", pattern_sim)

    # Data Engine
    data_engine = DataEngine()
    data_engine.add_exchange("bybit")
    data_engine.add_exchange("binance")
    data_engine.add_exchange("okx")
    data_engine.start()
    c.set("data_engine", data_engine)

    # Metrics & Health
    from core.monitoring import MetricsServer, get_healthcheck, get_metrics_registry

    metrics_registry = get_metrics_registry()
    healthcheck = get_healthcheck()
    healthcheck.register("data_engine", lambda: ...)  # simplified
    c.set("metrics_registry", metrics_registry)
    c.set("healthcheck", healthcheck)

    metrics_server = MetricsServer(host="0.0.0.0", port=9120)
    c.set("metrics_server", metrics_server)

    # Risk Engine
    from core.risk import get_risk_engine, SpreadRule, ATRRule, LiquidityRule, SessionRule

    risk_engine = get_risk_engine(shadow=False)
    risk_engine.add_rule(SpreadRule())
    risk_engine.add_rule(ATRRule())
    risk_engine.add_rule(LiquidityRule())
    risk_engine.add_rule(SessionRule())
    c.set("risk_engine", risk_engine)
    logger.info("[risk] RiskEngine active (shadow=False)")

    # Consensus Engine
    from core.consensus import get_consensus_engine, OpportunityRanking

    consensus_engine = get_consensus_engine(shadow=False)
    opportunity_rank = OpportunityRanking(window_minutes=10, top_k=3)
    c.set("consensus_engine", consensus_engine)
    c.set("opportunity_rank", opportunity_rank)
    logger.info("[consensus] ConsensusEngine active (shadow=False)")

    # OME
    from core.ome import get_ome

    ome = get_ome(shadow=False, capital=1000.0, risk_pct=0.01)
    c.set("ome", ome)
    logger.info("[ome] OME active (shadow=False)")

    # Learning Engine
    from core.learning import get_learning_engine

    learning = get_learning_engine(shadow=False)
    c.set("learning_engine", learning)
    logger.info("[learning] LearningEngine active (shadow=False)")

    # Event Bus
    from events import get_event_bus

    event_bus = get_event_bus()
    c.set("event_bus", event_bus)

    logger.info("[services] All background engines initialized")


async def phase_telegram(c: Container):
    """Phase 10: TelegramNotifier + handlers + V2 SignalEngine."""
    from core.signal.engine import SignalEngine as SignalEngineV2
    from alerts.telegram import TelegramNotifier, get_notifier
    from config import settings

    bus = c.require("bus")

    # V2 SignalEngine — active mode (shadow=False)
    consensus_engine = c.get("consensus_engine")
    decision_engine = c.get("decision_engine")
    risk_engine = c.get("risk_engine")
    ome = c.get("ome")
    learning_engine = c.get("learning_engine")

    signal_engine = SignalEngineV2(
        consensus_engine=consensus_engine,
        decision_engine=decision_engine,
        risk_engine=risk_engine,
        ome=ome,
        learning_engine=learning_engine,
        shadow=False,
    )
    c.set("signal_engine_v2", signal_engine)
    logger.info("[signal_v2] SignalEngineV2 active (shadow=False)")
    
    notifier = TelegramNotifier(
        token=settings.telegram_token,
        chat_id=str(settings.telegram_chat_id),
    )
    c.set("notifier", notifier)

    # SignalRecorder + WinRateChecker
    from storage.analytics import WinRateChecker, SignalRecorder, StatsReporter

    ticker_store = c.require("ticker_store")

    async def _get_price(symbol: str) -> float | None:
        t = ticker_store.get(symbol)
        return t.get("last_price") if t else None

    recorder = SignalRecorder(ticker_getter=_get_price)
    c.set("signal_recorder", recorder)

    winchecker = WinRateChecker(ticker_getter=_get_price)
    c.set("winchecker", winchecker)

    stats = StatsReporter(recorder.db, notifier)
    c.set("stats_reporter", stats)

    # Telegram handlers
    from alerts.handlers import setup_telegram_handlers
    from alerts.settings_db import UserSettingsDB
    from pathlib import Path

    db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
    settings_db_dir = Path(db_path).parent
    settings_db = UserSettingsDB(settings_db_dir / "user_settings.db")
    await settings_db.start()
    c.set("settings_db", settings_db)

    router = setup_telegram_handlers(settings_db, notifier)
    c.set("telegram_router", router)
    await notifier.attach_router(router)

    # Recent signals via notifier callback (replaces V1 dispatcher listeners)
    recent_signals: list[dict] = []

    async def _on_signal(sig):
        recent_signals.append({
            "symbol": sig.symbol,
            "signal_name": sig.signal_name,
            "score": round(sig.score, 1),
            "direction": sig.direction or "neutral",
        })
        if len(recent_signals) > 20:
            recent_signals.pop(0)
        router._recent_signals = recent_signals

    notifier.register_signal_listener(_on_signal)
    notifier.register_signal_listener(recorder.on_signal)

    logger.info("[telegram] Telegram layer ready (v0.10.0 — V2 active)")


async def phase_warmup(c: Container):
    """Phase 11: FeatureEngine warmup — загрузка исторических свечей через REST."""
    fe = c.get("feature_engine")
    if fe is None:
        logger.info("[warmup] No FeatureEngine — skipping warmup")
        return

    from core.features.calculators.ohlcv import OHLCVFeatureCalculator
    from core import Event
    import aiohttp

    ohlcv = next(
        (calc for calc in fe._calculators if isinstance(calc, OHLCVFeatureCalculator)),
        None,
    )
    if ohlcv is None:
        logger.warning("[warmup] OHLCVFeatureCalculator not found, skipping")
        return

    symbols = TOP_SYMBOLS

    async with aiohttp.ClientSession() as session:
        for sym in symbols:
            try:
                base, rest = sym.split("/", 1)
                quote = rest.split(":", 1)[0]
                raw = base + quote
                url = (
                    "https://api.bybit.com/v5/market/kline"
                    f"?category=linear&symbol={raw}&interval=1&limit=200"
                )
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    result = await resp.json()

                if result.get("retCode") != 0:
                    logger.warning("[warmup] Bybit API error for %s: %s", raw, result.get("retMsg"))
                    continue

                klines = result.get("result", {}).get("list", [])
                if not klines:
                    continue

                for k in reversed(klines):
                    candle = {
                        "t": int(k[0]),
                        "o": k[1], "h": k[2], "l": k[3], "c": k[4],
                        "v": k[5], "qv": k[6],
                    }
                    ev = Event(
                        channel=f"candles.1m.{sym}",
                        exchange="bybit", symbol=sym,
                        data=candle, ts=float(candle["t"]),
                    )
                    await ohlcv.on_event(ev)

                ohlcv._last_compute.pop(sym, None)
                await ohlcv.compute_if_expired(sym)
                for calc in fe._calculators:
                    if calc is not ohlcv:
                        calc._last_compute.pop(sym, None)
                        await calc.compute_if_expired(sym)

                logger.info("[warmup] Loaded %d 1m candles for %s", len(klines), sym)
            except Exception:
                logger.exception("[warmup] Failed to warmup %s", sym)

    warmed = sum(1 for s in symbols if ohlcv._last_candle.get((s, "1m")))
    logger.info("[warmup] FeatureEngine warmup complete (%d/%d symbols)", warmed, len(symbols))


async def phase_run(c: Container):
    """Phase 13: Подписка на каналы, запуск тикеров, ожидание shutdown."""

    # ── Подписка на каналы ──
    exchange = c.require("exchange")
    bus = c.require("bus")

    for ch in CHANNELS:
        params = "1" if ch == "candles" else None
        await exchange.subscribe(ch, TOP_SYMBOLS, params=params)
    for tf in EXTRA_TFS:
        await exchange.subscribe("candles", TOP_SYMBOLS, params=tf)
    await exchange.subscribe("orderbook", OB_SYMBOLS, params="50")
    logger.info("Subscribed: %d channels × %d symbols", len(CHANNELS), len(TOP_SYMBOLS))

    # ── Подписка Strategy Engine ──
    strategy_engine = c.get("strategy_engine")
    if strategy_engine:
        bus.subscribe("candles.*", strategy_engine.on_event)
        bus.subscribe("trades.*", strategy_engine.on_event)

    # ── Запуск компонентов ──
    await exchange.start()

    from scanner.trades import TradeScanner
    from scanner.candles import CandleScanner
    from scanner.ticker import TickerScanner, LiquidationScanner
    from scanner.orderbook import OrderBookScanner as _OBS

    scanners = [
        CandleScanner(bus=bus),
        TickerScanner(),
        TradeScanner(bus=bus),
        LiquidationScanner(),
        _OBS(bus=bus),
    ]
    for s in scanners:
        await s.start()
    logger.info("[scanner] All scanners started")

    # BatchFeatureUpdater
    bu = c.get("batch_updater")
    if bu:
        await bu.start()
        logger.info("[batch] BatchFeatureUpdater started (via phase_run)")

    # V2 pipeline — StrategyEngine → notifier (V1 SignalEngine removed in v0.10.0)
    notifier = c.require("notifier")
    await notifier.start()

    # Strategy Engine
    if strategy_engine:
        await strategy_engine.start()

    # State Engine (shadow loop)
    state_engine = c.get("state_engine")
    if state_engine:
        c.create_task(state_engine.start(), name="state_engine")

    # SignalRecorder + WinRateChecker + Stats
    recorder = c.get("signal_recorder")
    if recorder:
        await recorder.start()

    winchecker = c.get("winchecker")
    if winchecker:
        await winchecker.start()

    stats = c.get("stats_reporter")
    if stats:
        await stats.start()

    # Metrics server
    metrics_server = c.get("metrics_server")
    if metrics_server:
        await metrics_server.start()

    # Telegram polling
    await notifier.start_polling()

    # ── Фоновые тикеры ──
    c.create_task(_health_ticker(c), name="health")
    c.create_task(_session_ticker(c), name="session")
    c.create_task(_correlation_ticker(c), name="correlation")
    c.create_task(_breadth_ticker(c), name="breadth")
    c.create_task(_heatmap_ticker(c), name="heatmap")
    c.create_task(_rotation_ticker(c), name="rotation")
    c.create_task(_liquidity_ticker(c), name="liquidity")
    c.create_task(_trend_ticker(c), name="trend")
    c.create_task(_lifecycle_ticker(c), name="lifecycle")
    c.create_task(_market_ticker(c), name="market")
    c.create_task(_replay_ticker(c), name="replay")

    logger.info("All systems running. Press Ctrl+C to stop.")

    # ── Ожидание shutdown ──
    stop = asyncio.Future()

    def _signal_handler(signame):
        logger.info("Caught signal %s", signame)
        if not stop.done():
            stop.set_result(None)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop = asyncio.get_event_loop()
            loop.add_signal_handler(sig, lambda s=sig: _signal_handler(s.name))
        except NotImplementedError:
            # Windows
            signal.signal(sig, lambda s, f: _signal_handler(signal.Signals(s).name))

    await stop
    await c.shutdown("SIGTERM")


# ══════════════════════════════════════════════
#  Background tickers (вынесены из main)
# ══════════════════════════════════════════════


async def _health_ticker(c: Container):
    """Health check каждые 30 сек."""
    await asyncio.sleep(2)  # initial delay
    rs_engine = c.get("rs_engine")
    sector_engine = c.get("sector_engine")
    correlation_engine = c.get("correlation_engine")
    breadth_engine = c.get("breadth_engine")
    feature_engine = c.get("feature_engine")
    strategy_engine = c.get("strategy_engine")
    session_engine = c.get("session_engine")
    heatmap_engine = c.get("heatmap_engine")

    while True:
        await asyncio.sleep(30)
        try:
            _rs_top, _rs_bot = rs_engine.get_ranking("rs_5m_pct", top_n=3) if rs_engine else ([], [])
            rs_top = ",".join(r["symbol"].split("/")[0] for r in _rs_top) if _rs_top else "?"
            _sector_snap = sector_engine.scan(_rs_top) if _rs_top else None
            sector_leader = _sector_snap.leading_sector if _sector_snap and _sector_snap.sectors else "?"

            logger.info(
                "[health] strategies=%d session=%s rs_top=%s sector=%s feat_cache=%d",
                len(strategy_engine._strategies) if strategy_engine else 0,
                session_engine.session_name if session_engine else "?",
                rs_top,
                sector_leader,
                feature_engine.store.stats()["alive"] if feature_engine else 0,
            )

            b = breadth_engine.last_snapshot
            h = heatmap_engine.last_snapshot
            router = c.get("telegram_router")
            if router:
                router._health_cache = {
                    "uptime": f"{asyncio.get_event_loop().time() - c.loop_start_time:.0f}s",
                    "strategies": len(strategy_engine._strategies) if strategy_engine else 0,
                    "session": session_engine.session_name if session_engine else "?",
                    "rs_top": rs_top,
                    "sector": sector_leader,
                    "breadth": f"{b.pct_green}%" if b.total > 0 else "?",
                    "heatmap_volume": f"{h.top_volume[0]['symbol']} {h.top_volume[0]['volume_usdt']/1_000_000:.1f}M"
                    if h.top_volume else "?",
                }
        except Exception:
            logger.exception("[health] ticker error")


async def _session_ticker(c: Container):
    """Сессия — раз в 60 сек."""
    while True:
        await asyncio.sleep(60)
        se = c.get("session_engine")
        if se:
            try:
                await se.tick()
            except Exception:
                logger.exception("[session] ticker error")


async def _correlation_ticker(c: Container):
    """Корреляция + RS — раз в 2 сек."""
    while True:
        await asyncio.sleep(2)
        try:
            corr = c.get("correlation_engine")
            rs = c.get("rs_engine")
            ts = c.get("ticker_store")
            if not corr or not rs or not ts:
                continue
            prices = {}
            for sym in corr.TRACKED_SYMBOLS:
                t = ts.get(sym)
                if t:
                    price = t.get("lastPrice", t.get("last_price"))
                    if price and float(price) > 0:
                        prices[sym] = float(price)
            await corr.tick(prices)
            rs.update(prices)
        except Exception:
            logger.exception("[correlation] ticker error")


async def _breadth_ticker(c: Container):
    """Breadth — раз в 60 сек + сигнал."""
    while True:
        await asyncio.sleep(60)
        try:
            breadth = c.get("breadth_engine")
            engine = c.get("signal_engine")
            if breadth and engine:
                snap = breadth.tick()
                sig = breadth.to_signal()
                if sig:
                    await engine.push_signal(sig)
        except Exception:
            logger.exception("[breadth] ticker error")


async def _heatmap_ticker(c: Container):
    """Heatmap — раз в 60 сек + сигнал."""
    while True:
        await asyncio.sleep(60)
        try:
            hm = c.get("heatmap_engine")
            engine = c.get("signal_engine")
            if hm and engine:
                snap = hm.tick()
                sig = hm.to_signal()
                if sig:
                    await engine.push_signal(sig)
        except Exception:
            logger.exception("[heatmap] ticker error")


async def _rotation_ticker(c: Container):
    """Rotation — раз в 60 сек + сигнал."""
    while True:
        await asyncio.sleep(60)
        try:
            rs = c.get("rs_engine")
            rot = c.get("rotation_detector")
            engine = c.get("signal_engine")
            if rs and rot and engine:
                _rs_top, _rs_bot = rs.get_ranking("rs_5m_pct", top_n=10)
                if _rs_top:
                    snap = rot.tick(_rs_top)
                    sig = rot.to_signal()
                    if sig:
                        await engine.push_signal(sig)
        except Exception:
            logger.exception("[rotation] ticker error")


async def _liquidity_ticker(c: Container):
    """Liquidity zones — раз в 60 сек для TOP_SYMBOLS."""
    while True:
        await asyncio.sleep(60)
        try:
            liq = c.get("liquidity_engine")
            engine = c.get("signal_engine")
            if liq and engine:
                for sym in TOP_SYMBOLS:
                    snap = liq.analyze(sym)
                    sig = liq.to_signal(sym)
                    if sig:
                        await engine.push_signal(sig)
        except Exception:
            logger.exception("[liquidity] ticker error")


async def _trend_ticker(c: Container):
    """Trend strength — раз в 60 сек для TOP_SYMBOLS."""
    while True:
        await asyncio.sleep(60)
        try:
            tr = c.get("trend_engine")
            engine = c.get("signal_engine")
            if tr and engine:
                for sym in TOP_SYMBOLS:
                    snap = tr.analyze(sym)
                    sig = tr.to_signal(sym)
                    if sig:
                        await engine.push_signal(sig)
        except Exception:
            logger.exception("[trend] ticker error")


async def _lifecycle_ticker(c: Container):
    """Lifecycle — раз в 300 сек."""
    while True:
        await asyncio.sleep(300)
        try:
            engine = c.get("signal_engine")
            session_engine = c.get("session_engine")
            rs_engine = c.get("rs_engine")
            sector_engine = c.get("sector_engine")
            router = c.get("telegram_router")

            _rs = rs_engine.get_ranking("rs_5m_pct", top_n=3) if rs_engine else None
            rs_top = ",".join(r["symbol"].split("/")[0] for r in _rs[0]) if _rs and _rs[0] else "?"
            _sector_snap = sector_engine.scan(_rs[0]) if _rs and _rs[0] else None
            sector_leader = _sector_snap.leading_sector if _sector_snap and _sector_snap.sectors else "?"

            logger.info(
                "[lifecycle] session=%s signals=%d rs_top=%s sector=%s",
                session_engine.session_name if session_engine else "?",
                len(engine._signals) if engine else 0,
                rs_top,
                sector_leader,
            )
            if router:
                router._health_cache["lifecycle_active"] = True
        except Exception:
            logger.exception("[lifecycle] ticker error")


async def _market_ticker(c: Container):
    """Market analysis — раз в 300 сек + сигнал."""
    while True:
        await asyncio.sleep(300)
        try:
            corr = c.get("correlation_engine")
            analysis = c.get("analysis_engine")
            notifier = c.get("notifier")
            ts = c.get("ticker_store")
            breadth = c.get("breadth_engine")

            if not all([corr, analysis, notifier, ts, breadth]):
                continue

            prices = {}
            for sym in corr.TRACKED_SYMBOLS:
                t = ts.get(sym)
                if t:
                    price = t.get("lastPrice", t.get("last_price"))
                    if price and float(price) > 0:
                        prices[sym] = float(price)
            corr_snapshot = corr.get_snapshot()

            b = breadth.last_snapshot
            noise_level = 100 - b.pct_green if b.total > 0 else 50

            for sym in TOP_SYMBOLS:
                t = ts.get(sym)
                current_price = float(t.get("lastPrice", t.get("last_price", 0))) if t else 0
                if current_price <= 0:
                    continue
                snap = analysis.analyze(sym, current_price, prices, c.get("candle_store"))
                if snap:
                    sig = analysis.to_signal(sym, current_price, prices, corr_snapshot, noise_level)
                    if sig:
                        from core.explainable_ai import _fmt as _make_explanation

                        explanation = _make_explanation(sig, None, t)
                        if sig.meta is None:
                            sig.meta = {}
                        sig.meta["explanation"] = explanation
                        await notifier.send_signal(sig)
        except Exception:
            logger.exception("[market] ticker error")


async def _replay_ticker(c: Container):
    """Replay snapshot — раз в SNAPSHOT_INTERVAL сек."""
    while True:
        await asyncio.sleep(SNAPSHOT_INTERVAL)
        try:
            replay = c.get("replay_engine")
            ts = c.get("ticker_store")
            ob_store = c.get("ob_store")
            router = c.get("telegram_router")

            if not replay:
                continue

            for sym in OB_SYMBOLS:
                ticker = ts.get(sym) if ts else {}
                ob = ob_store.get_sync(sym) if ob_store else None
                replay.save_snapshot(
                    symbol=sym,
                    ticker=ticker,
                    orderbook={"bids": ob.bids, "asks": ob.asks} if ob else None,
                )
            if router:
                router._health_cache["replay_last"] = time.time()
        except Exception:
            logger.exception("[replay] ticker error")


# ══════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════

async def main():
    c = Container()

    phases = [
        (Phase.INFRASTRUCTURE, phase_infrastructure),
        (Phase.EXCHANGE,       phase_exchange),
        (Phase.STORAGE,        phase_storage),
        (Phase.FEATURES,       phase_features),
        (Phase.BATCH_UPDATER,  phase_batch_updater),
        (Phase.STATE,          phase_state),
        (Phase.CONTEXT,        phase_context),
        (Phase.DECISION,       phase_decision),
        (Phase.STRATEGY,       phase_strategy),
        (Phase.SERVICES,       phase_services),
        (Phase.TELEGRAM,       phase_telegram),
        (Phase.WARMUP,         phase_warmup),
        (Phase.RUN,            phase_run),
    ]

    for phase, fn in phases:
        await c.run_phase(phase, fn)


if __name__ == "__main__":
    asyncio.run(main())
