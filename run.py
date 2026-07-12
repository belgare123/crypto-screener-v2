"""Crypto Screener v2 — точка входа."""
import asyncio
import json
import logging
import signal
import sys
import time

from scanner.orderbook import OrderBookScanner

logger = logging.getLogger(__name__)

# ── Константы ──
SNAPSHOT_INTERVAL = 300  # сек


class ShutdownManager:
    """Управляет graceful shutdown."""

    def __init__(self):
        self._tasks: list[asyncio.Task] = []

    def register(self, task: asyncio.Task):
        self._tasks.append(task)

    async def shutdown(self, loop, signame=None):
        logger.info("Shutting down (signal=%s)...", signame)
        for t in self._tasks:
            t.cancel()
        await asyncio.sleep(0.5)


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Тише
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


async def main():
    _setup_logging()
    logger.info("=" * 60)
    logger.info("Crypto Screener v2 starting...")
    logger.info("=" * 60)

    # ── 1–2. Exchange + Data Bus ──
    from exchanges.bybit import BybitExchange
    from core import get_bus, MarketDataBus
    from core.features import get_feature_engine
    from core.features.calculators.whale import WhaleFeatureCalculator
    from core.features.calculators.ohlcv import OHLCVFeatureCalculator
    from core.features.calculators.indicators import IndicatorsFeatureCalculator
    from core.features.calculators.orderbook import OrderBookFeatureCalculator
    from core.features.calculators.market import MarketFeatureCalculator
    from core.features.calculators.volatility import VolatilityFeatureCalculator
    from context import ContextEngine, get_context_engine
    from strategies import StrategyEngine
    from core.state import StateEngine, get_state_engine
    from events import EventBus, get_event_bus, CandleEvent, WhaleEvent, VolumeEvent
    from core.exchanges import DataEngine
    from core.risk import RiskEngine, get_risk_engine, SpreadRule, ATRRule, LiquidityRule, SessionRule
    from core.consensus import ConsensusEngine, get_consensus_engine, OpportunityRanking
    from core.monitoring import MetricsServer, get_healthcheck, get_metrics_registry, HealthComponent, HealthStatus
    from core.ome import OME, get_ome
    from core.signal import SignalEngine, get_signal_engine
    from core.learning import LearningEngine, get_learning_engine

    exchange = BybitExchange()
    bus = get_bus()

    # ── 3. Data Streams ──
    from core.storage import get_candle_store, get_ticker_store, get_ob_store

    candle_store = get_candle_store()
    ticker_store = get_ticker_store()
    ob_store = get_ob_store()

    # ── 4. Scanners ──
    from scanner.trades import TradeScanner
    from scanner.candles import CandleScanner
    from scanner.ticker import TickerScanner, LiquidationScanner
    from scanner.orderbook import OrderBookScanner as _OBS

    scanners: list = [
        CandleScanner(bus=bus),
        TickerScanner(),
        TradeScanner(bus=bus),
        LiquidationScanner(),
        _OBS(bus=bus),
    ]

    # ── 5. Correlation Engine ──
    from core.correlation import CorrelationEngine

    correlation_engine = CorrelationEngine()

    # ── 6. RS / Strength ──
    from core.rotation import RotationDetector
    from core.relative_strength import RelativeStrengthEngine

    rs_engine = RelativeStrengthEngine(candle_store)

    # ── 7. Sector Scanner (нужен RotationDetector'у) ──
    from core.sector_scanner import SectorScannerEngine as SectorEngine

    sector_engine = SectorEngine()

    rotation_detector = RotationDetector(sector_engine=sector_engine)

    # ── 8. Heatmap ──
    from core.heatmap import HeatmapEngine

    def _ticker_all_getter():
        return ticker_store.all_sync()

    async def _liq_getter(minutes=5):
        return []

    heatmap_engine = HeatmapEngine(ticker_getter=_ticker_all_getter, liq_getter=_liq_getter)

    # ── 9. Liquidity Zones ──
    from core.liquidity_zones import LiquidityZoneEngine

    def _candle_getter(symbol: str, tf: str = "5m", limit: int = 100):
        return candle_store.get_sync(symbol, tf, limit)

    def _ob_getter(symbol: str):
        return ob_store.get_sync(symbol)

    liquidity_engine = LiquidityZoneEngine(candle_getter=_candle_getter, ob_getter=_ob_getter)

    # ── 10. Market Breadth ──
    from core.market_breadth import MarketBreadthEngine

    breadth_engine = MarketBreadthEngine(ticker_getter=_ticker_all_getter)

    # ── 11. Trend Strength ──
    from core.trend_strength import TrendStrengthEngine

    trend_engine = TrendStrengthEngine(candle_getter=_candle_getter)

    # ── 12. Signal Infrastructure ──
    from core.session import SessionEngine
    from core.signal_dna import DNAStore
    from core.signal_lifecycle import SignalLifecycleEngine
    from core.market_replay import MarketReplayEngine
    from core.market_analysis import ExpectedMoveEngine as MarketAnalysisEngine, RiskMeterEngine
    from core.ai_clustering import ClusteringEngine
    from core.pattern_similarity import PatternSimilarityEngine

    # 12a. Session
    session_engine = SessionEngine()

    # 12b. DNA store
    dna_store = DNAStore()

    # 12c. Lifecycle / Replay / Analysis
    lifecycle_engine = SignalLifecycleEngine()
    replay_engine = MarketReplayEngine()
    analysis_engine = MarketAnalysisEngine()

    # 12d. AI Clustering
    clustering = ClusteringEngine(dna_store)

    def _sector_for_symbol(sym: str) -> str:
        sn = sector_engine.get_snapshot()
        if sn and sn.symbol_sectors:
            return sn.symbol_sectors.get(sym, "other")
        return "other"

    # ── 12. Signal Engine ──
    from signals.engine import SignalEngine
    from signals.dispatcher import Dispatcher
    from scanner.volume_screener import VolumeScreener

    engine = SignalEngine(
        bus=bus,
        min_score=40.0,
    )

    # ── 12b. Feature Engine — централизованный слой признаков ──
    try:
        feature_engine = get_feature_engine()
        feature_engine.register(WhaleFeatureCalculator())
        feature_engine.register(OHLCVFeatureCalculator())
        feature_engine.register(IndicatorsFeatureCalculator())
        feature_engine.register(OrderBookFeatureCalculator())
        feature_engine.register(MarketFeatureCalculator())
        feature_engine.register(VolatilityFeatureCalculator())
        logger.info("[feat] FeatureEngine initialised: %d calculators, %d features",
                    len(feature_engine._calculators),
                    sum(len(c.feature_names) for c in feature_engine._calculators))
    except Exception:
        logger.exception("[feat] Failed to initialise FeatureEngine — continuing without it")
        feature_engine = None

    # ── 12c. State Engine (shadow mode) — агрегирует MarketState для всех символов ──
    state_engine = get_state_engine()
    logger.info("[state] StateEngine initialised (shadow=%s, interval=%.0fs)",
                state_engine.shadow, state_engine.interval)

    # ── 12d. Event Bus — типобезопасный слой над MarketDataBus ──
    event_bus = get_event_bus()
    logger.info("[events] EventBus initialised (shadow mode)")

    # ── 12e. Data Engine — нормализация потоков с нескольких бирж ──
    data_engine = DataEngine()
    data_engine.add_exchange("bybit")
    data_engine.add_exchange("binance")
    data_engine.add_exchange("okx")
    data_engine.start()
    logger.info("[data] DataEngine started — exchanges: bybit, binance, okx")

    # ── 12f. Risk Engine — пре-трейд фильтрация ──
    risk_engine = get_risk_engine(shadow=True)
    risk_engine.add_rule(SpreadRule())
    risk_engine.add_rule(ATRRule())
    risk_engine.add_rule(LiquidityRule())
    risk_engine.add_rule(SessionRule())
    logger.info("[risk] RiskEngine shadow=%s rules=%d", risk_engine.shadow, len(risk_engine._rules))

    # ── 12g. Consensus Engine — взвешенное голосование стратегий ──
    consensus_engine = get_consensus_engine(shadow=True)
    opportunity_rank = OpportunityRanking(window_minutes=10, top_k=3)
    logger.info("[consensus] ConsensusEngine shadow=%s ranking=10m/3top", consensus_engine.shadow)

    # ── 12h. Metrics & Healthcheck — Observability ──
    metrics_registry = get_metrics_registry()
    healthcheck = get_healthcheck()

    # Register healthcheck components
    healthcheck.register("state_engine", lambda: HealthComponent("state_engine", HealthStatus.HEALTHY))
    healthcheck.register("risk_engine", lambda: HealthComponent("risk_engine", HealthStatus.HEALTHY))
    healthcheck.register("data_engine", lambda: HealthComponent("data_engine", HealthStatus.HEALTHY))
    healthcheck.register("consensus_engine", lambda: HealthComponent("consensus_engine", HealthStatus.HEALTHY))
    logger.info("[monitoring] Healthcheck registered 4 components")

    # Start metrics server
    metrics_server = MetricsServer(host="0.0.0.0", port=9120)
    await metrics_server.start()

    # ── 12i. OME — Order Management Engine (shadow) ──
    ome = get_ome(shadow=True, capital=1000.0, risk_pct=0.01)
    logger.info("[ome] OME shaded mode — capital=%.0f risk_pct=%.2f", ome.capital, ome.sizer.risk_pct)

    # ── 12j. Learning Engine — winrate tracking + dynamic weights ──
    learning = get_learning_engine(shadow=True)
    logger.info("[learning] LearningEngine shadow=%s min_trades=%d",
               learning.shadow, learning.tracker.min_trades)

    # ── 13. Dispatcher ──
    from alerts.telegram import TelegramNotifier, get_notifier
    from config import settings

    notifier = TelegramNotifier(
        token=settings.telegram_token,
        chat_id=str(settings.telegram_chat_id),
    )

    dispatcher = Dispatcher(
        engine=engine,
        notifier=notifier,
        min_score=40.0,
    )

    # ── 13b. Strategy Engine + Context Engine (L3+L4) ──
    import strategies.momentum_v2  # noqa: F401 — триггерит @register_strategy

    context_engine = ContextEngine(
        feature_engine=feature_engine,
        session_engine=session_engine,
    )
    strategy_engine = StrategyEngine(
        feature_engine=feature_engine,
        context_engine=context_engine,
        signal_engine=engine,
    )
    strategy_engine.register_all()
    logger.info("[strategy] Context+Strategy Engine ready (%d strategies)", len(strategy_engine._strategies))

    # ── 13c. Volume Screener — мониторинг всех монет Bybit с объёмом >= $5M/день ──
    volume_screener = VolumeScreener(min_turnover=5_000_000)

    async def _volume_listener(ticker_row):
        """Слушатель новых монет, превысивших $5M объёма."""
        msg = (
                f"🔊 *{ticker_row.display}*\n"
                f"💰 Объём 24ч: ${ticker_row.turnover_m:.1f}M\n"
                f"📊 Цена: ${ticker_row.last_price:.4f}  |  Изм: {ticker_row.price_change_24h:+.2f}%"
            )
        try:
            await notifier.send_text(msg)
            logger.info("[volume_screener] notified new coin: %s ($%.1fM)", ticker_row.symbol, ticker_row.turnover_m)
        except Exception:
            logger.exception("[volume_screener] notify error for %s", ticker_row.symbol)

    volume_screener.add_listener(_volume_listener)

    # ── 14. Отладка — создать несколько сигналов ──
    from core import SignalResult

    # ── 15. Win Rate Checker ──
    from storage.analytics import WinRateChecker, SignalRecorder

    # ├─ топ-3 для health
    # └─ все 50 для ротации

    # ── 15b. Stats Reporter ──
    from storage.analytics import StatsReporter

    # ── 16. Pattern Similarity ──
    pattern_sim = PatternSimilarityEngine(dna_store)

    # ── 17. Data integration for tickers — read CandleStore ──
    # ticker_getter, чтобы можно было узнавать текущую цену из любого места

    async def _get_price(symbol: str) -> float | None:
        t = ticker_store.get(symbol)
        return t.get("last_price") if t else None

    recorder = SignalRecorder(ticker_getter=_get_price)
    await recorder.start()
    dispatcher.add_listener(recorder.on_signal)

    winchecker = WinRateChecker(ticker_getter=_get_price)
    await winchecker.start()

    stats = StatsReporter(recorder.db, notifier)
    await stats.start()

    # ── Запуск ──
    await exchange.start()
    for s in scanners:
        await s.start()
    await notifier.start()
    engine.load_signals()
    await engine.start()
    await dispatcher.start()
    await volume_screener.start()
    await strategy_engine.start()

    # Подписка на топ-10 пар по USDT (Bybit linear)
    top_symbols = [
        "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
        "XRP/USDT:USDT", "DOGE/USDT:USDT", "ADA/USDT:USDT",
        "AVAX/USDT:USDT", "DOT/USDT:USDT", "LINK/USDT:USDT",
        "SUI/USDT:USDT",
    ]
    channels = ["candles", "trades", "ticker", "liquidation"]
    for ch in channels:
        # candles требуют таймфрейм (Bybit: 1, 3, 5, 15, 30, 60, 120, 240 etc — без 'm')
        params = "1" if ch == "candles" else None
        await exchange.subscribe(ch, top_symbols, params=params)
    # Дополнительные таймфреймы свечей для MTF анализа
    for tf in ("5", "15"):
        await exchange.subscribe("candles", top_symbols, params=tf)
    logger.info("Subscribed to %d channels × %d symbols (incl. 5m, 15m)", len(channels), len(top_symbols))

    # Подписка Strategy Engine на события шины
    bus.subscribe("candles.*", strategy_engine.on_event)
    bus.subscribe("trades.*", strategy_engine.on_event)
    logger.info("[strategy] StrategyEngine subscribed to candles.*, trades.*")

    # OrderBook только для BTC и ETH (самый дорогой стрим)
    await exchange.subscribe("orderbook", ["BTC/USDT:USDT", "ETH/USDT:USDT"], params="50")
    logger.info("Subscribed to orderbook for BTC & ETH")

    # Настройка Telegram handlers
    from config import settings
    from alerts.handlers import setup_telegram_handlers
    from alerts.settings_db import UserSettingsDB
    from pathlib import Path

    settings_db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
    settings_db_dir = Path(settings_db_path).parent
    settings_db = UserSettingsDB(settings_db_dir / "user_settings.db")
    router = setup_telegram_handlers(settings_db, notifier)
    await notifier.attach_router(router)
    await notifier.start_polling()

    # Recent signals for Telegram menu (после router)
    _recent_signals: list[dict] = []

    async def _on_signal_listener(sig):
        _recent_signals.append({
            "symbol": sig.symbol,
            "signal_name": sig.signal_name,
            "score": round(sig.score, 1),
            "direction": sig.direction or "neutral",
        })
        if len(_recent_signals) > 20:
            _recent_signals.pop(0)
        router._recent_signals = _recent_signals

    dispatcher.add_listener(_on_signal_listener)

    logger.info("All systems running. Press Ctrl+C to stop.")

    # ── 17b. Warmup FeatureEngine — исторические свечи через REST API ──
    async def warmup_feature_engine(fe, symbols: list[str]):
        """Загрузить последние 200 1m свечей через Bybit REST для OHLCV буфера."""
        import aiohttp
        from core import Event

        try:
            ohlcv = next(
                (c for c in fe._calculators if isinstance(c, OHLCVFeatureCalculator)),
                None
            )
            if ohlcv is None:
                logger.warning("[warmup] OHLCVFeatureCalculator not found, skipping")
                return

            async with aiohttp.ClientSession() as session:
                for sym in symbols:
                    try:
                        # BTC/USDT:USDT → BTCUSDT (Bybit REST формат)
                        base, rest = sym.split("/", 1)
                        quote = rest.split(":", 1)[0]
                        raw = base + quote
                        url = (
                            "https://api.bybit.com/v5/market/kline"
                            f"?category=linear&symbol={raw}&interval=1&limit=200"
                        )
                        async with session.get(
                            url, timeout=aiohttp.ClientTimeout(total=10)
                        ) as resp:
                            result = await resp.json()

                        if result.get("retCode") != 0:
                            logger.warning(
                                "[warmup] Bybit API error for %s: %s",
                                raw, result.get("retMsg"),
                            )
                            continue

                        klines = result.get("result", {}).get("list", [])
                        if not klines:
                            continue

                        # REST возвращает newest-first → разворачиваем для хронологии
                        for k in reversed(klines):
                            candle = {
                                "t": int(k[0]),   # timestamp ms
                                "o": k[1],         # open
                                "h": k[2],         # high
                                "l": k[3],         # low
                                "c": k[4],         # close
                                "v": k[5],         # volume
                                "qv": k[6],        # turnover
                            }
                            ev = Event(
                                channel=f"candles.1m.{sym}",
                                exchange="bybit",
                                symbol=sym,
                                data=candle,
                                ts=float(candle["t"]),
                            )
                            await ohlcv.on_event(ev)

                        # Форсируем compute для OHLCV
                        ohlcv._last_compute.pop(sym, None)
                        await ohlcv.compute_if_expired(sym)

                        # Форсируем compute для остальных калькуляторов (indicators, volatility ...)
                        for calc in fe._calculators:
                            if calc is not ohlcv:
                                calc._last_compute.pop(sym, None)
                                await calc.compute_if_expired(sym)

                        logger.info(
                            "[warmup] Loaded %d 1m candles for %s",
                            len(klines), sym,
                        )
                    except Exception:
                        logger.exception("[warmup] Failed to warmup %s", sym)

            warmed = sum(
                1 for s in symbols if ohlcv._last_candle.get((s, "1m"))
            )
            logger.info(
                "[warmup] FeatureEngine warmup complete (%d/%d symbols)",
                warmed, len(symbols),
            )
        except Exception:
            logger.exception("[warmup] Warmup failed")

    if feature_engine:
        asyncio.create_task(warmup_feature_engine(feature_engine, top_symbols))

    # ── 18b. State Engine — shadow-loop ──
    asyncio.create_task(state_engine.start())

    # ── Health-check ──
    loop_start_time = asyncio.get_event_loop().time()

    async def health_check():
        while True:
            await asyncio.sleep(30)
            # RS top
            _rs_top, _rs_bot = rs_engine.get_ranking("rs_5m_pct", top_n=3) if rs_engine else ([], [])
            rs_top = ",".join(r["symbol"].split("/")[0] for r in _rs_top) if _rs_top else "?"
            # Sector leader
            _sector_snap = sector_engine.scan(_rs_top) if _rs_top else None
            sector_leader = _sector_snap.leading_sector if _sector_snap and _sector_snap.sectors else "?"
            logger.info(
                "[health] exchange=%s signals=%d session=%s corr_alignment=%s rs_top=%s sector=%s breadth=%s feat_cache=%d feat_calcs=%d strategies=%d",
                exchange.name,
                len(engine._signals),
                session_engine.session_name,
                round(correlation_engine.get_alignment(), 2) if correlation_engine.get_snapshot().pairs else "?",
                rs_top,
                sector_leader,
                f"{breadth_engine.last_snapshot.pct_green}%" if breadth_engine.last_snapshot.total > 0 else "?",
                feature_engine.store.stats()["alive"] if feature_engine else 0,
                sum(c.compute_count for c in feature_engine._calculators) if feature_engine else 0,
                len(strategy_engine._strategies),
            )
            # Push health data to Telegram handlers
            b = breadth_engine.last_snapshot
            h = heatmap_engine.last_snapshot
            router._health_cache = {
                'uptime': f'{asyncio.get_event_loop().time() - loop_start_time:.0f}s',
                "signals": len(engine._signals),
                "session": session_engine.session_name,
                "corr_alignment": round(correlation_engine.get_alignment(), 2) if correlation_engine.get_snapshot().pairs else "?",
                "rs_top": rs_top,
                "sector": sector_leader,
                "breadth": f"{b.pct_green}%" if b.total > 0 else "?",
                "heatmap_volume": f"{h.top_volume[0]['symbol']} {h.top_volume[0]['volume_usdt']/1_000_000:.1f}M" if h.top_volume else "?",
                "events": getattr(engine, "_event_count", "?"),
                "db_status": "\u2713",
            }

    async def session_ticker():
        """Проверка сессии раз в 60 секунд."""
        while True:
            await asyncio.sleep(60)
            try:
                await session_engine.tick()
            except Exception:
                logger.exception("[session] ticker error")

    async def correlation_ticker():
        """Обновление корреляций и RS раз в 2 секунды."""
        while True:
            await asyncio.sleep(2)
            try:
                prices = {}
                for sym in correlation_engine.TRACKED_SYMBOLS:
                    t = ticker_store.get(sym)
                    if t:
                        price = t.get("lastPrice", t.get("last_price"))
                        if price and float(price) > 0:
                            prices[sym] = float(price)
                await correlation_engine.tick(prices)
                rs_engine.update(prices)
            except Exception:
                logger.exception("[correlation] ticker error")

    async def breadth_ticker():
        """Обновление ширины рынка раз в 60 секунд + отправка сигнала."""
        while True:
            await asyncio.sleep(60)
            try:
                snap = breadth_engine.tick()
                sig = breadth_engine.to_signal()
                if sig:
                    await engine.push_signal(sig)
                    logger.info("[breadth] signal: %.0f/100 (green=%.1f%% / red=%.1f%%)",
                                sig.score, snap.pct_green, snap.pct_red)
            except Exception:
                logger.exception("[breadth] ticker error")

    async def heatmap_ticker():
        """Обновление тепловой карты раз в 60 секунд."""
        while True:
            await asyncio.sleep(60)
            try:
                snap = heatmap_engine.tick()
                sig = heatmap_engine.to_signal()
                if sig:
                    await engine.push_signal(sig)
                    logger.info("[heatmap] signal: %.0f/100 (liq_total=%.0f, triggers=%s)",
                                sig.score, snap.liq_total_notional, sig.meta.get("triggers", []) if sig.meta else [])
            except Exception:
                logger.exception("[heatmap] ticker error")

    async def rotation_ticker():
        """Обновление ротации раз в 60 секунд + отправка сигнала."""
        while True:
            await asyncio.sleep(60)
            try:
                _rs_top, _rs_bot = rs_engine.get_ranking("rs_5m_pct", top_n=10) if rs_engine else ([], [])
                if _rs_top:
                    snap = rotation_detector.tick(_rs_top)
                    sig = rotation_detector.to_signal()
                    if sig:
                        await engine.push_signal(sig)
                        logger.info("[rotation] signal: %.0f/100 (%s \u2192 %s, %s)",
                                    sig.score,
                                    sig.meta.get("previous_leader", "?"),
                                    sig.meta.get("current_leader", "?"),
                                    sig.direction)
            except Exception:
                logger.exception("[rotation] ticker error")

    async def liquidity_ticker():
        """Обновление зон ликвидности раз в 60 секунд для всех символов."""
        while True:
            await asyncio.sleep(60)
            tracked = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT",
                       "DOGE/USDT:USDT", "ADA/USDT:USDT", "AVAX/USDT:USDT", "DOT/USDT:USDT",
                       "LINK/USDT:USDT", "SUI/USDT:USDT"]
            for sym in tracked:
                try:
                    snap = liquidity_engine.analyze(sym)
                    sig = liquidity_engine.to_signal(sym)
                    if sig:
                        await engine.push_signal(sig)
                        logger.info("[liquidity] %s signal: %.0f/100 (S=%.2f R=%.2f)",
                                    sym.split("/")[0], sig.score,
                                    snap.nearest_support, snap.nearest_resistance)
                except Exception:
                    logger.exception("[liquidity] error analyzing %s", sym)

    async def trend_ticker():
        """Обновление трендов раз в 60 секунд для всех символов + отправка сигнала."""
        while True:
            await asyncio.sleep(60)
            tracked = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT",
                       "DOGE/USDT:USDT", "ADA/USDT:USDT", "AVAX/USDT:USDT", "DOT/USDT:USDT",
                       "LINK/USDT:USDT", "SUI/USDT:USDT"]
            for sym in tracked:
                try:
                    snap = trend_engine.analyze(sym)
                    sig = trend_engine.to_signal(sym)
                    if sig:
                        await engine.push_signal(sig)
                        logger.info("[trend] %s signal: %.0f/100 (%.1f/%.1f)",
                                    sym.split("/")[0], sig.score, snap.trend_strength, snap.momentum)
                except Exception:
                    logger.exception("[trend] error analyzing %s", sym)

    async def lifecycle_ticker():
        """Принудительный клининг мёртвых сигналов раз в 300 секунд + health."""
        while True:
            await asyncio.sleep(300)
            try:
                # RS snapshot snapshot — для логов
                _rs = rs_engine.get_ranking("rs_5m_pct", top_n=3) if rs_engine else None
                rs_top = ",".join(r["symbol"].split("/")[0] for r in _rs[0]) if _rs and _rs[0] else "?"
                # Sector
                _sector_snap = sector_engine.scan(_rs[0]) if _rs and _rs[0] else None
                sector_leader = _sector_snap.leading_sector if _sector_snap and _sector_snap.sectors else "?"
                logger.info(
                    "[lifecycle] status: session=%s signals=%d rs_top=%s sector=%s",
                    session_engine.session_name,
                    len(engine._signals),
                    rs_top,
                    sector_leader,
                )
                # push in health cache
                router._health_cache["lifecycle_active"] = True
            except Exception:
                logger.exception("[lifecycle] ticker error")

    async def market_ticker():
        """Рын. анализ раз в 300 секунд + объяснение."""
        correlation_snapshot = None  # to capture
        while True:
            await asyncio.sleep(300)
            # Собираем snapshot со всех двигателей
            prices = {}
            for sym in correlation_engine.TRACKED_SYMBOLS:
                t = ticker_store.get(sym)
                if t:
                    price = t.get("lastPrice", t.get("last_price"))
                    if price and float(price) > 0:
                        prices[sym] = float(price)
            correlation_snapshot = correlation_engine.get_snapshot()

            # Оценка noise level через liquidity / breadth
            b = breadth_engine.last_snapshot
            noise_level = 100 - b.pct_green if b.total > 0 else 50

            # Анализ по каждому символу
            tracked = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT",
                       "DOGE/USDT:USDT", "ADA/USDT:USDT", "AVAX/USDT:USDT", "DOT/USDT:USDT",
                       "LINK/USDT:USDT", "SUI/USDT:USDT"]
            for sym in tracked:
                try:
                    t = ticker_store.get(sym)
                    current_price = float(t.get("lastPrice", t.get("last_price", 0))) if t else 0
                    if current_price <= 0:
                        continue
                    snap = analysis_engine.analyze(sym, current_price, prices, candle_store)
                    if snap:
                        sig = analysis_engine.to_signal(sym, current_price, prices, correlation_snapshot, noise_level)
                        if sig:
                            await engine.push_signal(sig)
                            from core.explainable_ai import _fmt as _make_explanation
                            explanation = _make_explanation(sig, None, t)
                            # Сохраняем объяснение в meta
                            if sig.meta is None:
                                sig.meta = {}
                            sig.meta["explanation"] = explanation
                            logger.info("[market] %s signal: %.0f/100 %s — %s",
                                        sym.split("/")[0], sig.score, sig.direction,
                                        explanation[:100] if explanation else "?")
                except Exception:
                    logger.exception("[market] error analyzing %s", sym)

            # noise health
            router._health_cache[f"noise_{noise_level:.0f}"] = True

            # risk signal with aggregated risk score
            risk_signals = [s for s in analysis_engine._signals if hasattr(s, "risk_score")]
            if risk_signals:
                avg_risk = sum(s.risk_score for s in risk_signals) / len(risk_signals)
                logger.info("[market] risk: average %.1f%%", avg_risk * 100)
                router._health_cache[f"risk_avg_{avg_risk:.2f}"] = True

    async def replay_ticker():
        """Snapshot market state every SNAPSHOT_INTERVAL seconds."""
        while True:
            await asyncio.sleep(SNAPSHOT_INTERVAL)
            try:
                # Сохраняем снэпшоты для BTC & ETH (есть orderbook)
                for sym in ("BTC/USDT:USDT", "ETH/USDT:USDT"):
                    ticker = ticker_store.get(sym) if hasattr(ticker_store, 'get') else {}
                    ob = ob_store.get_sync(sym)
                    replay_engine.save_snapshot(
                        symbol=sym,
                        ticker=ticker,
                        orderbook={"bids": ob.bids, "asks": ob.asks} if ob else None,
                    )
                logger.info("[replay] snapshots saved for BTC & ETH")
                router._health_cache["replay_last"] = time.time()
            except Exception:
                logger.exception("[replay] ticker error")

    async def volume_screener_ticker():
        """Сканирование всех монет Bybit по объёму раз в 5 минут + сводка раз в 30 мин."""
        cycle = 0
        while True:
            await volume_screener.run_loop()
            cycle += 1
            # Каждый 6-й цикл (30 мин) — отправляем сводку топ-10
            if cycle % 6 == 0:
                try:
                    all_qualifying = volume_screener._known_symbols
                    if all_qualifying:
                        tickers = await volume_screener.scan()
                        if tickers:
                            top10 = tickers[:10]
                            lines = ["📊 *Топ объёмов 24ч (Bybit USDT)*\n"]
                            for i, t in enumerate(top10, 1):
                                lines.append(
                                    f"{i}. {t.display:15s}  ${t.turnover_m:>7.1f}M  "
                                    f"${t.last_price:>8.4f}  {t.price_change_24h:+.2f}%"
                                )
                            lines.append(f"\nВсего {len(tickers)} монет с объёмом ≥ $5M")
                            await notifier.send_text("\n".join(lines))
                except Exception:
                    logger.exception("[volume_screener] summary error")

    # ── 18. Запуск фоновых тасок ──
    ticker_tasks = [
        asyncio.create_task(health_check()),
        asyncio.create_task(session_ticker()),
        asyncio.create_task(correlation_ticker()),
        asyncio.create_task(breadth_ticker()),
        asyncio.create_task(heatmap_ticker()),
        asyncio.create_task(rotation_ticker()),
        asyncio.create_task(liquidity_ticker()),
        asyncio.create_task(trend_ticker()),
        asyncio.create_task(lifecycle_ticker()),
        asyncio.create_task(market_ticker()),
        asyncio.create_task(replay_ticker()),
        asyncio.create_task(volume_screener_ticker()),
    ]

    shutdown_mgr = ShutdownManager()
    for t in ticker_tasks:
        shutdown_mgr.register(t)

    # ── 19. Ожидание сигнала завершения ──
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

    # ── 20. Graceful shutdown ──
    await shutdown_mgr.shutdown(loop, "SIGTERM")
    await notifier.stop()
    await exchange.stop()
    logger.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
