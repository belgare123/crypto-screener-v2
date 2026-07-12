# MIGRATION PLAN V3 — Архитектура «Knowledge First»

> Философия: система строится вокруг знаний о рынке, а не вокруг сигналов.
> Сигнал — лишь финальный Output на выходе из цепочки engines.
>
> Метод миграции: **Strangler Fig + Feature Toggles** — каждый новый engine
> проходит shadow → compare → active → retired. V1 работает до полного
> замещения без даунтайма.

---

## 1. Текущая архитектура (as-is)

```
Bybit WS → MarketDataBus (Event)
                │
      ┌─────────┼──────────────┐
      ▼         ▼              ▼
  Scanner   Core Engines   Signal Engine (V1)
  ───────   ────────────   ─────────────
  Candles   Correlation    54 @register-сигнала
  Ticker    MarketBreadth  ────  каждый сам:
  Trades    Rotation            считает фичи →
  OrderBook Heatmap             детектит событие →
  Liq.      TrendStrength       решает score →
  Volume    LiquidityZones      шлёт в Dispatcher
  Screener  RelativeStrength
            SessionEngine
            FeatureEngine (L2) → Store
            ContextEngine (L3)
            StrategyEngine (L4) → momentum_v2
```

**Проблемы:**

1. **54 сигнала = 54 дублирующих вычислений** — большинство считают одно и то же
2. **Event Engine отсутствует** — «whale вошёл» детектится в 4+ местах
3. **State Engine отсутствует** — Momentum во флэте тратит CPU на вычисления, которые можно было пропустить
4. **Risk Engine = грубый cooldown** — нет spread/ATR/liquidity фильтров
5. **Order Management отсутствует** — нет позиционирования, SL/TP, размера сделки
6. **Learning Engine отсутствует** — WinRateChecker есть, но ни на что не влияет
7. **Storage размазан** — CandleBuffer/TickerStore (scanner/), SQLite (database/), FeatureStore (core/)
8. **Нет разделения «событие» / «торговое решение»**

---

## 2. Целевая архитектура (to-be)

```
┌──────────────────────────────────────────────────────────────┐
│                    Bybit WS (Data Sources)                     │
│   trades · candles · ticker · orderbook · liq. · funding · OI │
└──────────────────────────┬───────────────────────────────────┘
                           │ [сырые события по каналам шины]
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                    1. DATA ENGINE                              │
│   role: чистый сбор + нормализация + публикация в шину         │
│   modules: exchanges/bybit, exchanges/binance, exchanges/okx  │
│   + Multi Exchange Merge: объединение потоков по символу       │
│     → единый MarketDataStream(BTC, Bybit+Binance+OKX)         │
│     → медиана/средневзвешенная для консенсуса цены            │
│   pub: data.trade.{sym}, data.candle.{sym}.{tf}, data.ob.{sym}│
│   freq: per WS message (real-time)                             │
└──────────────────────────┬───────────────────────────────────┘
                           │ [raw: Trade, Candle, OB, Liq, Funding, OI]
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                    2. STORAGE ENGINE                           │
│   role: единый слой хранения (in-mem + SQLite)                │
│   modules:                                                    │
│     CandleStore   ─ deque(maxlen=200) + SQLite history        │
│     TradeStore    ─ rolling window (last 1000 trades)         │
│     OBStore       ─ snapshot + depth per symbol               │
│     FeatureStore  ─ TTL-indexed key-value (существует)         │
│     AnalyticsStore ─ SQLite → WinRate, PF, signal_log          │
│   freq: read/write per tick, compaction every 5min             │
└──────────────────────────┬───────────────────────────────────┘
                           │ [structured: OHLCV, trades, ob snapshots]
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                    3. FEATURE ENGINE                           │
│   role: единственный вычислитель признаков                     │
│   calculators: (категории — независимые, lazy compute)          │
│     Trend ─ EMA slope, ADX, MACD, VWAP                         │
│     Momentum ─ RSI, ROC, Stochastic, MFI                       │
│     Volume ─ Volume Profile (POC, VAH, VAL), Delta Volume,     │
│               CVD, BuySellRatio, TakerFlow                     │
│     Volatility ─ ATR, Bollinger, VOLD, Keltner                  │
│     Liquidity ─ Spread, Depth, Walls, Queue Imbalance,          │
│                  Liquidity Sweep                                │
│     Flow ─ Footprint Metrics (Bid×Ask), Delta, Absorption      │
│     Derivatives ─ Funding Rate, OI change, Basis               │
│     Market ─ Dominance, Correlation, Breadth                   │
│     Sector ─ L1, L2, DeFi, RWA, MEME, DePIN momentum          │
│     Behavior ─ Noise level, Participation (Retail/Balanced/     │
│                 Institutional), Microstructure                  │
│   freq: per tick (lazy - only if reader requests) + cache     │
│         1m OHLCV → compute every 60s                          │
│         OB features → compute per WS update (rate-limited 1/s)│
└──────────────────────────┬───────────────────────────────────┘
                           │ [features: dict keyed by symbol+tf+name]
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                    4. STATE ENGINE                             │
│   role: единое состояние рынка per symbol + global            │
│   detectors:                                                  │
│     TrendDetector ─ bull / bear / flat / ranging              │
│     VolDetector ─ low / normal / expansion / compression      │
│     CycleDetector ─ accumulation / distribution               │
│     RegimeDetector ─ risk-on / risk-off / panic / euphoria    │
│     NoiseDetector ─ noise_level = f(volatility, volume,       │
│                      spread); signal/noise ratio              │
│     ParticipationDetector ─ retail / balanced / institutional │
│   state: MarketState(symbol, trend, vol, cycle, regime,       │
│          noise, participation)                                │
│   freq: timer 60s + triggered by Event Engine on big moves    │
│   storage: dict[symbol] → State(symbol, trend, vol, cycle)    │
└──────────────────────────┬───────────────────────────────────┘
                           │ [state: per-symbol + global]
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                    5. EVENT ENGINE                             │
│   role: детекция значимых событий (публикует MarketEvent)     │
│   detectors:                                                  │
│     WhaleDetector ─ whale.entered[severity, volume, side]     │
│     VolDetector ─ volume.exploded[ratio, baseline]            │
│     CandleDetector ─ consecutive.candles[direction, count]    │
│                     gap.opened[size, direction]                │
│     OBDetector ─ wall.appeared[depth, side]                   │
│                  spread.widened[current, baseline]             │
│     LiqDetector ─ liquidation.cascade[total_notional]         │
│     VolatilityDetector ─ volatility.expanded[ratio]           │
│     TradeFlowDetector ─ cluster.buy[size, count]              │
│                         whale.accum[symbol, total_vol]        │
│   freq: per WS message (async, non-blocking)                  │
│   pub: event.{type}.{sym} → EventBus                          │
└──────────────────────────┬───────────────────────────────────┘
                           │ [MarketEvent(type, severity, data)]
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                    6. CONTEXT ENGINE (FACADE)                  │
│   role: единый интерфейс для стратегии — НЕ вычисляет сам      │
│   provides:                                                   │
│     ctx.features ─ из FeatureStore                            │
│     ctx.state ─ из StateEngine                                │
│     ctx.events ─ из EventEngine (за последний период)         │
│     ctx.session ─ из SessionEngine                            │
│     ctx.breadth ─ из BreadthEngine                            │
│     ctx.correlation ─ из CorrelationEngine                    │
│   правило: ContextEngine только агрегирует                    │
└──────────────────────────┬───────────────────────────────────┘
                           │ [StrategyContext — единый объект]
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                    7. STRATEGY ENGINE                          │
│   role: только принятие решения на основе готовых данных       │
│   strategies:                                                 │
│     Momentum ─ вес событий momentum + состояние тренда        │
│     Reversal ─ экстремумы + события разворота                 │
│     Breakout ─ волатильность + объём + пробитие               │
│     Whale ─ крупные сделки + ликвидность                      │
│     Liquidity ─ зоны ликвидности + стоп-ханты                 │
│     RelativeStrength ─ RS momentum + секторная ротация        │
│     AI/ML ─ кластеризация + explainable AI                    │
│     Consensus ─ обёртка для V2 взвешивания                    │
│   правило: стратегия НЕ содержит:                              │
│     ❌ расчёт фич → Feature Engine                             │
│     ❌ детекцию → Event Engine                                 │
│     ❌ состояние → State Engine                                │
│     ❌ взвешивание → Consensus Engine                          │
│   method: evaluate(ctx) → Vote(direction, confidence, reason) │
└──────────────────────────┬───────────────────────────────────┘
                           │ [Vote[] — массив голосов]
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                    8. CONSENSUS ENGINE                         │
│   role: взвешенное голосование стратегий                      │
│   logic:                                                     │
│     Momentum BUY(70) + Breakout BUY(60) = BUY 65             │
│     + Liquidity NONE + Whale SELL(40) = BUY 48               │
│   weights: динамические (изначально равные, LearningEngine    │
│            корректирует)                                      │
│   output: ConsensusResult(direction, score, votes, reasons)   │
│   + Opportunity Ranking: накопление сигналов N минут →        │
│     сортировка по confidence → топ-K (по умолчанию 10)       │
│     вместо отправки каждого сигнала сразу                      │
│   storage: ranking buffer per period (config: rank_window=300s│
│            rank_max_signals=10)                               │
└──────────────────────────┬───────────────────────────────────┘
                           │ [ConsensusResult]
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                    9. RISK ENGINE (pre-OME)                    │
│   role: фильтр — можно ли вообще торговать?                   │
│   filters:                                                    │
│     SpreadRisk ─ spread > 0.1% → BLOCK                        │
│     ATRRisk ─ ATR / price > 5% → REDUCE(weight=0.5)          │
│     LiquidityRisk ─ 24h volume < $1M → BLOCK                  │
│     VolatilityRisk ─ аномальный VOLD → BLOCK                  │
│     SessionRisk ─ не торговать в первые 5min сессии           │
│   output: RiskDecision.ALLOW / RiskDecision.BLOCK /            │
│           RiskDecision.REDUCE(weight)                          │
└──────────────────────────┬───────────────────────────────────┘
                           │ [RiskDecision]
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                 10. ORDER MANAGEMENT ENGINE (NEW)              │
│   role: рассчитать сделку — не просто сигнал, а ордер         │
│   components:                                                 │
│                                                                  │
│   ┌─ PositionSizer ──────────────────────────────────────┐    │
│   │  qty = (capital × risk_per_trade) / (ATR × sl_mult)  │    │
│   │  Пример: $10k × 1% / ($200 × 2) = 0.25 BTC          │    │
│   └──────────────────────────────────────────────────────┘    │
│                                                                  │
│   ┌─ RiskManager ─────────────────────────────────────────┐    │
│   │  SL = entry - ATR × sl_mult  (или тех. уровень)       │    │
│   │  TP1 = entry + ATR × rr_ratio  (partial close 50%)    │    │
│   │  TP2 = entry + ATR × rr_ratio × 2  (partial close 50%)│    │
│   └──────────────────────────────────────────────────────┘    │
│                                                                  │
│   ┌─ PositionTracker ─────────────────────────────────────┐    │
│   │  symbol → Position(id, side, qty, entry, sl, tp, pnl) │    │
│   │  Проверка SL/TP каждый тик                             │    │
│   └──────────────────────────────────────────────────────┘    │
│                                                                  │
│   ┌─ OrderExecutor ───────────────────────────────────────┐    │
│   │  Market entry → Bybit REST                             │    │
│   │  Limit SL/TP → Bybit REST                              │    │
│   │  Partial close → OCO или лимитник                      │    │
│   └──────────────────────────────────────────────────────┘    │
│                                                                  │
│   config:                                                      │
│     capital: 10000          # USDT                             │
│     risk_per_trade: 0.01    # 1%                               │
│     sl_mult: 2.0            # 2 × ATR                          │
│     rr_ratio: 1.5           # take-profit 1:1.5               │
│     partial_close_pct: 0.5  # 50% at TP1                      │
│     max_open_positions: 3                                      │
│                                                                  │
│   freq: per signal + per tick (SL/TP check)                   │
└──────────────────────────┬───────────────────────────────────┘
                           │ [OrderRequest(symbol, side, qty, sl, tp)]
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                 11. SIGNAL ENGINE                              │
│   role: финальный слой — отправить результат пользователю     │
│   output: SignalResult(symbol, direction, score, reasons,      │
│             qty, sl, tp, status, id)                           │
│   lifecycle:                                                   │
│     SignalStatus.DETECTED → сигнал обнаружен                    │
│     SignalStatus.CONFIRMED → прошёл фильтры и порог            │
│     SignalStatus.ACTIVE → отправлен пользователю               │
│     SignalStatus.WEAKENING → confidence падает                 │
│     SignalStatus.EXPIRED → цена ушла / время вышло             │
│   anti-spam: cooldown per symbol (остаётся)                   │
│   update: при изменении confidence → update(id, new_status)    │
│           вместо нового сигнала, если id уже существует        │
└──────────────────────────┬───────────────────────────────────┘
                           │ [SignalResult]
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                 12. LEARNING ENGINE                            │
│   role: оценка результатов → корректировка весов              │
│   logic:                                                      │
│     Через N=30 мин после сигнала:                             │
│       if price >= TP1 → WIN                                   │
│       elif price <= SL → LOSS                                 │
│       else → PENDING (ещё не closed)                          │
│     Скользящее окно: последние 100 сигналов                   │
│     Метрики: WinRate, Profit Factor, Expectancy               │
│     Обновление весов Consensus: не чаще 1 раза в час          │
│     Защита от переобучения:                                   │
│       - если WinRate стратегии < 30% → вес = 0               │
│       - если StdDev(weights) > 0.3 → rebalance               │
│       - rollback: если после обновления весов WinRate         │
│         упал → откат к предыдущим                             │
│   freq: async — не в hot path сигнала                         │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. Маппинг текущего кода → целевые Engine'ы

### DATA ENGINE

| Файл | Судьба |
|------|--------|
| `exchanges/bybit/__init__.py` | ✅ остаётся |
| `exchanges/binance/__init__.py` | ✅ остаётся |
| `exchanges/okx/__init__.py` | ✅ остаётся |
| `scanner/candles.py:CandleBuffer` | ❌ → Storage |
| `scanner/ticker.py:TickerStore` | ❌ → Storage |
| `scanner/orderbook.py:OrderBookState` | ❌ → Storage |
| `scanner/trades.py` | ❌ → Storage |

### STORAGE ENGINE

**Типы хранилищ:**
- **SQLite:** `CandleStore` (история свечей), `AnalyticsStore` (signal_log, WinRate)
- **In-memory (deque):** `TradeStore` (rolling window 1000), `OBStore` (snapshot + depth)
- **In-memory (TTL dict):** `FeatureStore` (lazy compute + TTL 60s OHLCV / 1s OB)

| Файл | Целевой модуль | Тип | Фаза |
|------|---------------|-----|------|
| `scanner/candles.py` | `storage/candle_store.py` | SQLite + deque | Ph.5 |
| `scanner/ticker.py` | `storage/ticker_store.py` | In-mem TTL | Ph.5 |
| `scanner/orderbook.py` | `storage/ob_store.py` | In-mem deque | Ph.5 |
| `scanner/trades.py` | `storage/trade_store.py` | In-mem deque | Ph.5 |
| `core/features/store.py` | ✅ расширить TTL | In-mem TTL | Ph.0 |
| `database/models.py:SignalLog` | `storage/analytics_store.py` | SQLite | Ph.5 |
| `storage/analytics.py` | `storage/analytics_store.py` | SQLite | Ph.7 |

### FEATURE ENGINE

**Кэширование и TTL:**
- OHLCV-фичи (RSI, EMA, ATR): recompute каждые 60s (TTL), либо по триггеру новой свечи
- OrderBook-фичи (spread, depth, walls): recompute per WS update, rate-limit 1/s
- TradeFlow-фичи (CVD, buy/sell ratio): recompute per trade, rate-limit 1/s
- **Принцип:** lazy — фичи вычисляются только если есть потребитель (стратегия/движок)
- **Хранилище:** `FeatureStore` (TTL-indexed dict) — автоматическая инвалидация по TTL

| Файл | Судьба |
|------|--------|
| `core/features/calculators/ohlcv.py` | ✅ |
| `core/features/calculators/indicators.py` | ✅ |
| `core/features/calculators/volatility.py` | ✅ |
| `core/features/calculators/orderbook.py` | ✅ |
| `core/features/calculators/whale.py` | ✅ |
| `core/features/calculators/market.py` | ✅ |
| `signals/candle_technicals.py` | ⛔ расчёты → Feature, детекция → Event |
| `signals/volume.py` | ⛔ → Event |
| `signals/trade_flow.py` | ⛔ → Event |
| `signals/hybrid.py` | ⛔ → Event + Feature |
| `signals/orderbook_signals.py` | ⛔ → Event |
| `core/correlation.py` | ⛔ → Feature |
| `core/relative_strength.py` | ⛔ → Feature |
| `core/trend_strength.py` | ⛔ → Feature + State |

### STATE ENGINE

| Файл | Судьба |
|------|--------|
| (новый) `core/state/trend.py` | ⭐ создать |
| (новый) `core/state/volatility.py` | ⭐ создать |
| (новый) `core/state/cycle.py` | ⭐ создать |
| (новый) `core/state/regime.py` | ⭐ создать |
| (новый) `core/state/engine.py` | ⭐ создать |
| `context/market_context.py:volatility_state` | ⛔ переместить расчёты в State |
| `momentum_v2._factor_consecutive` (flat detection) | ⛔ → State |

### EVENT ENGINE

**Dead-letter channel (DLQ):**
- Если детектор падает или событие malformed → `DLQ.push(event, error)`
- DLQ мониторится: alert при >10 ошибок/мин
- Метрики: `event_engine.dlq_count`, `event_engine.dlq_top_errors`

| Файл | Судьба |
|------|--------|
| (новый) `events/base.py` (MarketEvent, EventBus) | ⭐ создать |
| (новый) `events/whale.py` | ⭐ создать |
| (новый) `events/volume.py` | ⭐ создать |
| (новый) `events/candles.py` | ⭐ создать |
| (новый) `events/ob.py` | ⭐ создать |
| (новый) `events/volatility.py` | ⭐ создать |
| (новый) `events/liquidation.py` | ⭐ создать |
| (новый) `events/trade_flow.py` | ⭐ создать |

### ORDER MANAGEMENT ENGINE (NEW)

**Интеграция с биржей:**
- `OrderExecutor` отправляет ордера через **Bybit REST API** (не WS — REST гарантирует подтверждение)
- Эндпоинты: `POST /v5/order/create` (market entry), `POST /v5/order/create` (limit SL/TP), `POST /v5/order/cancel`
- **Безопасность:** API-ключи с торговыми правами, отдельный лимит rate-limit
- **Shadow-mode:** OME рассчитывает qty/SL/TP, НО не отправляет ордера. Режим переключения: `shadow → dry_run (лог ордера) → live`

| Модуль | Файл |
|--------|------|
| PositionSizer | `core/oms/sizer.py` |
| RiskManager (SL/TP) | `core/oms/risk.py` |
| PositionTracker | `core/oms/tracker.py` |
| OrderExecutor | `core/oms/executor.py` |
| Engine | `core/oms/engine.py` |

---

## 4. V1→V2: механизм плавного переключения (Feature Flags)

### 4.1 Режим каждого сигнала

Каждый из 54 V1-сигналов управляется toggle в config:

```yaml
# config.yaml — V1→V2 migration
migration:
  signals:
    rsi_v2:
      mode: compare         # shadow | compare | active | retired
      tolerance_pct: 5.0    # допустимое расхождение score
    whale_v2:
      mode: shadow
    # ... для каждого сигнала
```

| Режим | V1 output | V2 output | Влияние |
|-------|-----------|-----------|---------|
| **shadow** | ✅ в Telegram | ✅ в лог | V1 работает, V2 логирует, пользователь не видит V2 |
| **compare** | ✅ в Telegram | ✅ в лог + сравниваем | Оба работают, скрипт сверяет score/direction |
| **active** | ❌ заблокирован | ✅ в Telegram | V2 заменил V1 |
| **retired** | ❌ код удалён | ✅ в Telegram | Финальная стадия — код V1 удалён |

### 4.2 Критерий «совпадение достаточное»

```
✅ signal.rsi_v2: V1=72.3 vs V2=70.1 → diff=3.1% < tol=5.0% → PASS
❌ signal.whale_v2: V1=85.0 vs V2=40.2 → diff=52% > tol=5.0% → FAIL
```

Если не проходит tolerance — сигнал НЕ переключается в active.
Идёт аудит расхождений.

### 4.3 Структура конфига

```yaml
migration:
  default_tolerance_pct: 5.0
  global_mode: compare     # можно переключить все сразу
  signals:
    v1_pullback:
      mode: active
      tolerance_pct: 10.0  # более шумный сигнал
    v1_volume_spike:
      mode: shadow         # ещё не готов
    momentum_v2:
      mode: active         # V2 стратегия — сразу active
```

### 4.4 Защита от двойной отправки

В режиме **compare** оба движка гоняются, но в Telegram идёт ТОЛЬКО V1.
V2 блокируется на уровне Dispatcher.
Когда режим меняется на **active** — V1 блокируется, V2 разблокируется.
Никогда не бывает 2 сигнала в Telegram.

---

## 5. Частота обновления каждого Engine

| Engine | Триггер | Частота | Комментарий |
|--------|---------|---------|-------------|
| **Data** | WS сообщение | real-time | каждый тик, блокирующих операций нет |
| **Storage** | WS сообщение + timer | real-time + 5min compaction | запись быстрая, compaction в фоне |
| **Feature** | запрос стратегии/движка | lazy + cache TTL | 1m OHLCV → 60s TTL, OB → 1s rate-limit |
| **State** | timer + event trigger | 60s (timer) / по событию | конфигурируется per type |
| **Event** | WS сообщение | per tick | легковесная детекция, линейная сложность |
| **Context** | запрос стратегии | per evaluate() | фасад → всегда актуален |
| **Strategy** | событие/состояние для symbol | по необходимости | не гоняется циклом — триггерная модель |
| **Consensus** | после всех стратегий | per batch | синхронно |
| **Risk** | per ConsensusResult | per signal | < 1ms |
| **OME** | signal + per tick (SL/TP) | per signal + per tick (1s) | SL/TP check каждую секунду |
| **Signal** | per RiskDecision | per signal | < 1ms |
| **Learning** | async, через N мин | ~30 min per signal | не в hot path |

---

## 6. Мультисимвольная модель

### 6.1 Изоляция per symbol

Каждый engine хранит состояние per symbol в `dict[str, T]`:

```python
class StateEngine:
    _states: dict[str, PerSymbolState]  # keyed by "BTC/USDT:USDT"
    
    async def tick_symbol(self, symbol: str, features: FeatureStore):
        state = self._states.get(symbol) or PerSymbolState(symbol)
        state.trend = await self._trend.detect(symbol, features)
        state.volatility = await self._vol.detect(symbol, features)
        # ...
```

### 6.2 Параллельная обработка

```python
async def process_symbol(self, symbol: str):
    features = self.feature_engine.get(symbol)
    state = self.state_engine.get(symbol)
    events = self.event_engine.get_recent(symbol)
    ctx = self.context_engine.build(symbol, features, state, events)
    
    votes = []
    for strategy in self.strategies:
        vote = await strategy.evaluate(ctx)
        votes.append(vote)
    
    consensus = self.consensus_engine.vote(votes)
    risk = self.risk_engine.filter(consensus)
    if risk == RiskDecision.ALLOW:
        order = self.oms.calculate(consensus, risk)
        signal = self.signal_engine.dispatch(order)
```

Символы обрабатываются через `asyncio.gather` с ограничением конкурентности (semaphore=10).

### 6.3 Глобальное vs per symbol

| Engine | Per Symbol | Global |
|--------|-----------|--------|
| State | trend, vol, cycle | regime (risk-on/off) |
| Event | все детекторы | - |
| Context | features, state | breadth, correlation, session |
| Strategy | evaluate() зависит от symbol | - |
| Risk | spread, liquidity per symbol | volatility risk global |
| OME | position per symbol | capital global |

---

## 7. План миграции Storage Layer (Dual-Write)

### 7.1 Двухфазная запись

```
WS поток → старые хранилища (scanner/*) → новый StorageEngine
                │                              │
                ▼                              ▼
         V1 сигналы читают             V2 engines читают
         из старого кода               из нового StorageEngine
```

В течение переходного периода каждый входящий WS-пакет пишется в ОБА хранилища:
- Старое: `scanner/candles.py`, `scanner/ticker.py`, etc.
- Новое: `storage/candle_store.py`, `storage/ticker_store.py`, etc.

### 7.2 ETL-миграция исторических данных

Скрипт `scripts/migrate_storage.py`:

```python
# читает из старых структур
old_buffers = scanner.candles.CandleBuffer._buffers
for key, deque_data in old_buffers.items():
    symbol, tf = parse_key(key)
    for candle in deque_data:
        new_store.add_candle(symbol, tf, candle)

# переносит из SQLite
old_logs = await SignalLog.get_all()
for log in old_logs:
    await new_store.add_signal_log(log.to_dict())
```

### 7.3 Верификация

После миграции: `assert old_store.get(sym) == new_store.get(sym)` для случайной выборки.

### 7.4 Отключение старых хранилищ

1. Dual-write включён (оба пишут)
2. Все потребители переключены на новый StorageEngine
3. Dual-write выключен (старый не пишет)
4. Старый код удалён

---

## 8. Уточнение LearningEngine

### 8.1 Механизм оценки

```python
class LearningEngine:
    _window_size = 100        # последние 100 сигналов
    _min_signals = 20          # минимальная выборка для весов
    _weight_update_interval = 3600  # 1 час
    
    async def evaluate_signal(self, signal: SignalResult):
        """Через 30 мин после сигнала."""
        price_now = await self._get_price(signal.symbol)
        
        if signal.direction == "buy":
            outcome = "win" if price_now >= signal.tp1 else \
                      "loss" if price_now <= signal.sl else "pending"
        else:
            outcome = "win" if price_now <= signal.tp1 else \
                      "loss" if price_now >= signal.sl else "pending"
        
        await self._record_outcome(signal.strategy_name, outcome)
```

### 8.2 Метрики (скользящее окно)

```python
class StrategyMetrics:
    def __init__(self):
        self.wins = deque(maxlen=100)
        self.losses = deque(maxlen=100)
    
    @property
    def win_rate(self) -> float:
        total = len(self.wins) + len(self.losses)
        return len(self.wins) / total if total > 0 else 0.5
    
    @property
    def profit_factor(self) -> float:
        gross_profit = sum(self.wins)
        gross_loss = sum(abs(l) for l in self.losses)
        return gross_profit / gross_loss if gross_loss > 0 else 1.0
    
    @property
    def expectancy(self) -> float:
        return (self.win_rate * self.avg_win) - \
               ((1 - self.win_rate) * self.avg_loss)
```

### 8.3 Обновление весов (Adaboost-like)

```python
async def update_weights(self):
    """Не чаще 1 раза в час."""
    if time.time() - self._last_weight_update < 3600:
        return
    
    total_wr = sum(m.win_rate for m in self._metrics.values())
    n = len(self._metrics)
    
    for name, metrics in self._metrics.items():
        if metrics.total < self._min_signals:
            continue
        
        # Базовый вес = win_rate / sum(win_rates)
        weight = metrics.win_rate / total_wr if total_wr > 0 else 1/n
        
        # Защита от переобучения
        if metrics.win_rate < 0.3:
            weight = 0  # стратегия мертва
        if metrics.std > 0.3:
            weight = weight * 0.5  # нестабильная → half
        
        self._weights[name] = weight
    
    # Нормализация
    total = sum(self._weights.values())
    for name in self._weights:
        self._weights[name] /= total
    
    # Механизм отката
    old_weights = self._prev_weights
    new_weights = dict(self._weights)
    
    # Тест: если после обновления WinRate упадет → откат
    self._prev_weights = old_weights  # временно
    test_wr = await self._simulate_weights(old_weights)
    new_wr = await self._simulate_weights(new_weights)
    
    if new_wr < test_wr:
        self._weights = old_weights  # откат
        logger.warning("[learning] rollback: new weights degraded WR")
    else:
        self._prev_weights = new_weights
        self._last_weight_update = time.time()
```

---

## 9. Диаграмма потоков данных

```
TICK_LOOP (per symbol)
═══════════════════════════════════════════════════════════════

WS: trade/candle/ob arrives
    │
    ├──→ [Data Engine] нормализует → Event(channel, symbol, data)
    │
    ├──→ [Storage Engine] пишет в CandleStore / TradeStore / OBStore
    │
    ├──→ [Feature Engine] считает (если TTL истёк или запрос)
    │    └──→ FeatureStore.set(key, value)
    │
    ├──→ [Event Engine] детектит: whale? volume? wall?
    │    └──→ EventBus.publish(MarketEvent(type, symbol, severity, data))
    │
    ├──→ [State Engine] (только если timer≥60s или Event триггер)
    │    └──→ _states[symbol].update(trend, vol, cycle)
    │
    └──→ система решает: нужно ли переоценивать стратегии?
         └── если да:
              │
              ├──→ [Context Engine] собирает StrategyContext
              │    = FeatureStore.get(symbol) + StateEngine.get(symbol)
              │    + EventEngine.get_recent(symbol, max_age=300)
              │
              ├──→ [Strategy Engine] = [Strat1.evaluate(ctx), Strat2.evaluate(ctx), ...]
              │    → [Vote(direction, confidence), ...]
              │
              ├──→ [Consensus Engine] weighted vote
              │    → ConsensusResult(direction=BUY, score=72, votes=[...])
              │
              ├──→ [Risk Engine] pre-OME →
              │    ALLOW / BLOCK / REDUCE
              │
              ├──→ [ORDER MANAGEMENT ENGINE]
              │    │
              │    ├── PositionSizer: qty = capital × risk% / (ATR × sl_mult)
              │    ├── RiskManager: SL = entry - ATR×2, TP = entry + ATR×3
              │    ├── PositionTracker: уже есть открытая позиция?
              │    │   если да → skip (одна позиция на symbol)
              │    │   если нет → open new
              │    └── OrderExecutor: Bybit REST API (market + limit SL/TP)
              │
              └──→ [Signal Engine]
                   → SignalResult → Telegram
                   → Notifier (в будущем — webhook)


SL/TP MONITOR (per second, per open position)
═══════════════════════════════════════════════

[OME.Tracker] проверяет все Position(symbol, entry, sl, tp)
    │
    ├── price <= SL → market close (full)
    ├── price >= TP1 → partial close 50%
    ├── price >= TP2 → close remaining 50%
    └── иначе → hold


LEARNING LOOP (async, ~30 min per signal)
═══════════════════════════════════════════

[Signal dispatched] → ждём 30 мин
    │
    ├──→ [Learning Engine] проверяет price vs SL/TP
    ├──→ обновляет скользящее окно (last 100)
    ├──→ если час прошёл: пересчитывает веса Consensus
    └──→ если веса упали: откат
```

---

## 10. Мониторинг и метрики (Observability)

Каждый Engine публикует метрики в единый `MetricsRegistry`. Метрики агрегируются в `observability/` (Phase 8).

| Engine | Метрики | Тип |
|--------|---------|-----|
| Data Engine | `ws.msg_per_sec`, `ws.lag_ms`, `ws.reconnects` | Gauge, Counter |
| Storage Engine | `storage.read_latency`, `storage.write_ops`, `cache.hit_ratio` | Histogram, Counter |
| Feature Engine | `feature.compute_latency`, `feature.ttl_expired`, `feature.cache_hit` | Histogram, Gauge |
| State Engine | `state.changes`, `state.current_regime`, `state.compute_ms` | Gauge, Counter |
| Event Engine | `event.produced`, `event.dlq_count`, `event.dlq_top_errors` | Counter, Gauge |
| Context Engine | `context.builds_per_sec`, `context.build_latency` | Histogram |
| Strategy Engine | `strategy.votes_per_strategy`, `strategy.compute_latency` | Histogram per strategy |
| Consensus Engine | `consensus.final_confidence`, `consensus.buy_vs_sell_ratio` | Gauge |
| Risk Engine | `risk.blocked_signals_by_reason`, `risk.passed_ratio` | Counter per reason |
| OME | `ome.position_count`, `ome.qty_per_symbol`, `ome.order_latency` | Gauge, Histogram |
| Signal Engine | `signal.sent_count`, `signal.symbol_coverage` | Counter |
| Learning Engine | `learning.win_rate`, `learning.pf`, `learning.weight_updates` | Gauge per strategy |

**Инструментация:**
- Каждый Engine получает `MetricsRegistry` конструктором
- Метрики доступны через HTTP-эндпоинт `/metrics` (Prometheus-совместимый)
- Alert при: dlq > 10/min, consensus_score < 10 за час, OME position_count > max
- Логи: structured JSON (движок, событие, длительность)

---

## 11. Скорректированные сроки

| Фаза | Описание | Оценка | Gate |
|------|----------|--------|------|
| **0. Аудит** | Матрица 54 сигналов, 3 быстрых победы | **5–7 д** | Матрица утверждена, 3 дубликата в compare |
| **1. State** | StateEngine + отвязка ContextEngine | **1–2 нед** | 48h shadow-mode без расхождений |
| **2. Event** | EventBus + 6–8 детекторов | **2–3 нед** | 48h shadow-mode, каждый event совпадает с V1 |
| **3. Risk** | Spread/ATR/Liquidity/Session фильтры | **1 нед** | Ни один реальный сигнал не заблокирован ложно |
| **4. Consensus** | Vote → Weighted → ConsensusResult | **1 нед** | Consensus не хуже V1 solo за 48h |
| **5. Storage** | CandleStore, TradeStore, OBStore + dual-write | **2 нед** | Все stores dual-write, read совпадает |
| **6. V1→V2** | 54 сигнала → Event+Strategy | **3–4 нед** | Все сигналы в active, V1 в retired |
| **7. OME** | PositionSizer, RiskManager, Tracker, Executor | **2–3 нед** | OME shadow-mode: qty/SL/TP корректны |
| **8. Learning** | Оценка, веса, откат | **1–2 нед** | Веса стабильны, rollback работает |
| **Docs** | ADR, архитектурное руководство | **1 нед** | Документация написана |
| **Итого** | | **14–20 нед** | |

---

## 12. Тестирование

### 12.1 Unit-тесты

Каждый engine: изолированные тесты с моками входящих данных.

```python
# tests/test_state_engine.py
async def test_trend_detection():
    engine = TrendDetector()
    features = mock_features(ema_ratio=1.02, adx=25)
    state = await engine.detect("BTC/USDT:USDT", features)
    assert state == "bull"
```

### 12.2 Replay regression

```python
# tests/test_replay_compare.py
"""
Скрипт: берёт запись WS-событий за N часов,
прогоняет через V1 и V2 pipelines,
сравнивает output по каждому сигналу.
"""

replay = load_replay("data/replay/2026-07-11.json")
engine_v1 = build_v1_pipeline()
engine_v2 = build_v2_pipeline()

mismatches = []
for event in replay:
    r1 = await engine_v1.process(event)
    r2 = await engine_v2.process(event)
    
    for sig_v1, sig_v2 in zip(r1.signals, r2.signals):
        diff = abs(sig_v1.score - sig_v2.score)
        if diff > config.tolerance_pct:
            mismatches.append({
                "signal": sig_v1.name,
                "ts": event.ts,
                "v1_score": sig_v1.score,
                "v2_score": sig_v2.score,
                "diff_pct": diff,
            })

assert len(mismatches) / total_signals < 0.05, \
    f"{len(mismatches)}/{total_signals} mismatches"
```

### 12.3 Shadow-mode validation

Каждый engine в shadow должен:
1. Не влиять на V1 output
2. Не ронять latency более чем на 10%
3. Не потреблять более 20% CPU дополнительно

---

## 13. Риски и Mitigation

| Риск | P | I | Стратегия |
|------|---|---|-----------|
| **Потеря уникальной эвристики V1** | H | H | Фаза 0: каждый сигнал audit. Shadow-mode: сравнение output. Tolerance-pct: не переключаем если расходится |
| **Двойные сигналы в Telegram** | M | H | Feature flag guard: V2 включается только когда V1 выключен. Compare mode → только лог |
| **OME откроет лишнюю позицию** | M | H | PositionTracker: одна позиция на symbol. Shadow-mode: только расчёт, без ордеров |
| **StateEngine неверно определит тренд** | M | M | Shadow-mode 48h. Сравнение output с ContextEngine. При расхождении — откат |
| **Нагрузка dual-run на CPU/RAM** | M | M | Shadow-mode: rate-limit тяжёлые вычисления. Мониторинг до включения |
| **Торговля в неликвидные часы** | L | H | SessionRisk: не торговать первые 5 мин сессии, последние 5 мин |
| **LearningEngine переобучится на шуме** | M | M | Минимум 100 сигналов для веса. WinRate < 30% → вес = 0. Hourly update |
| **Срыв сроков** | H | M | Каждая фаза имеет gate. Если gate не пройден → фаза продлевается. Бюджет +30% |

---

## 14. Документация и обучение

### 14.1 Артефакты

| Документ | Где | Когда |
|----------|-----|-------|
| `MIGRATION_PLAN_V3.md` | корень репо | ✅ сейчас |
| `docs/ARCHITECTURE.md` | `docs/` | после Фазы 0 |
| `docs/ENGINES.md` | `docs/` | после Фазы 2 |
| `docs/HOW_TO_ADD_STRATEGY.md` | `docs/` | после Фазы 4 |
| `docs/HOW_TO_ADD_EVENT.md` | `docs/` | после Фазы 2 |
| `docs/OME_CONFIG.md` | `docs/` | после Фазы 7 |
| `ADR/*.md` (Architecture Decision Records) | `docs/adr/` | по ходу |

### 14.2 Обучение

1. После Фазы 2: воркшоп «Event Engine + State Engine» для команды
2. После Фазы 6: code review всей V1→V2 миграции
3. После Фазы 7: OME walkthrough (безопасность, лимиты, тесты)

### 14.3 ADR (Architecture Decision Records)

Пример:
```
# ADR-001: State Engine refresh model
Date: 2026-07-12
Status: Accepted

Context: StateEngine needs to know market regime.
We considered timer-only (60s) vs event-triggered.

Decision: hybrid — timer 60s + event trigger.
Events with severity > 0.7 force immediate state recalc.

Consequences: +complexity but -latency on big moves.
```

---

## 15. Порядок действий прямо сейчас

```
✅ [ФАЗА 0] Аудит 54 сигналов: 
    ✅ docs/signal_audit_matrix.md — 54 сигнала, 17 категорий, 30 файлов
    ✅ Дубликаты: RSI (3×), Whale (3×), Liquidation (4×), OrderBook (5×) → compare mode docs/signal_audit_matrix.md#2
    ✅ tests/replay_compare.py — V1↔V2 regression (tolerance 5%, gate FAIL on mismatch)
    ✅ Gate 0: пройден — аудит завершён, 4 пары идентифицированы для shadow-mode

✅ [ФАЗА 1] State Engine:
    ✅ core/state/ — пакет создан
    ✅ core/state/market_state.py — MarketStateSnapshot (Trend·Noise·Vol·Liq·Participation)
    ✅ core/state/trend.py — TrendDetector (EMA alignment + ADX + MACD → strong/weak/sideways)
    ✅ core/state/noise.py — NoiseDetector (body/wick ratio + directionality + range expansion)
    ✅ core/state/participation.py — ParticipationDetector (liq analysis + volume → retail/balanced/institutional)
    ✅ core/state/engine.py — StateEngine (сборщик: parallel detect → MarketStateSnapshot → FeatureStore)
    🔲 Shadow-mode 48h → запустить `state_engine.start()` в run.py после теста

✅ [ФАЗА 2] Event Engine:
    ✅ events/__init__.py — пакет (экспорт CandleEvent, WhaleEvent, VolumeEvent)
    ✅ events/base.py — MarketEvent (typed base) + EventBus (typed subscribe/publish, symbol filter)
    ✅ events/candles.py — CandleEvent (OHLCV + body/range/ratio properties)
    ✅ events/whale.py — WhaleEvent (from_data factory, cluster support)
    ✅ events/volume.py — VolumeEvent (surge/drop, buy/sell ratio)
    ✅ EventBus test (publish→route→handler symbol filtering) ✓
    ✅ Wiring в run.py (get_event_bus())

✅ [ФАЗА 3] Data Engine:
    ✅ core/exchanges/base.py — NormalizedEvent, TradeData, CandleData, OB, Liq, Funding, OI
    ✅ core/exchanges/bybit_norm.py — BybitNormalizer (trade, candle, ob, liq, funding)
    ✅ core/exchanges/binance_norm.py — BinanceNormalizer (trade, candle, ob, liq)
    ✅ core/exchanges/okx_norm.py — OKXNormalizer (trade, candle, ob)
    ✅ core/exchanges/deribit_norm.py — DeribitNormalizer (trade, candle, ob)
    ✅ core/exchanges/merge.py — MultiExchangeMerge (consolidated price, median, VWAP, % participation)
    ✅ core/exchanges/engine.py — DataEngine (router: raw→normalizer→bus, start/shadow)
    ✅ Wiring в run.py (add_exchange bybit/binance/okx)
    ✅ Tests: все 4 нормализатора + мерж + DataEngine ✓

✅ [ФАЗА 4] Risk Engine:
    ✅ core/risk/__init__.py — пакет
    ✅ core/risk/models.py — RiskVerdict (ALLOW/BLOCK/REDUCE), RiskReason, RiskResult, RiskContext
    ✅ core/risk/rules.py — RiskRule (интерфейс)
    ✅ core/risk/rules_spread.py — SpreadRule (spread vs ATR ratio)
    ✅ core/risk/rules_atr.py — ATRRule (volatility filter: high/low/surge)
    ✅ core/risk/rules_liquidity.py — LiquidityRule (min volume, min OI)
    ✅ core/risk/rules_session.py — SessionRule (Asia, Friday close, block hours)
    ✅ core/risk/engine.py — RiskEngine (evaluate all rules, shadow mode, stats)
    ✅ Wiring в run.py (shadow=True, 4 rules)
    ✅ Tests: Normal ALLOW, Spread BLOCK, ATR BLOCK, Liquidity BLOCK, Shadow, Session ✓

✅ [ФАЗА 5] Consensus Engine:
    ✅ core/consensus/__init__.py — пакет
    ✅ core/consensus/models.py — SignalVote, ConsensusResult (direction, score, confidence, meaningful), OpportunityWindow
    ✅ core/consensus/engine.py — ConsensusEngine (weighted vote by winrate, regime_multiplier, buy/sell ratio)
    ✅ core/consensus/rank.py — OpportunityRanking (window N min, top-K by best_score, purge expired)
    ✅ Wiring в run.py (shadow=True, OpportunityRanking 10m/3top)
    ✅ Tests: Weighted BUY, Winrate floor, NEUTRAL, Regime multiplier, Ranking top-2, Shadow, Meaningful filter ✓

✅ [ФАЗА 6] Observability:
    ✅ core/monitoring/__init__.py — пакет
    ✅ core/monitoring/registry.py — MetricsRegistry (Counter/Gauge/Histogram + Prometheus text export)
    ✅ core/monitoring/health.py — Healthcheck (register check_fn, overall status, caching)
    ✅ core/monitoring/server.py — MetricsServer (HTTP /metrics + /health + /, asyncio-based, no FastAPI)
    ✅ Wiring в run.py (MetricsServer :9120, 4 healthcheck components)
    ✅ Tests: Counter inc, Gauge set/inc/dec, Histogram observe+prometheus output, Health healthy/unhealthy ✓

✅ [ФАЗА 7] OME — Order Management Engine:
    ✅ core/ome/__init__.py — пакет
    ✅ core/ome/models.py — OrderSide, OrderType, OrderStatus, Order, Position (с pnl_pct, distance_to_sl/tp)
    ✅ core/ome/sizer.py — PositionSizer (risk%, sl_mult, quantize, min/max)
    ✅ core/ome/risk_manager.py — RiskManager (SL/TP по ATR, min_distance_pct, RRR)
    ✅ core/ome/tracker.py — PositionTracker (open/close/update_price, pnl, total_exposure)
    ✅ core/ome/executor.py — OrderExecutor (shadow mode, exchange.create_order placeholder)
    ✅ core/ome/engine.py — OME facade (execute_signal: sizer→risk→track→exec)
    ✅ Wiring в run.py (shadow=True, capital=1000, risk_pct=0.01)
    ✅ Tests: Sizer, RiskManager, Tracker, Executor, OME facade ✓

✅ [ФАЗА 8] Learning Engine:
    ✅ core/learning/__init__.py — пакет
    ✅ core/learning/models.py — WinRateEntry (won, duration_hours), StrategyStats (winrate, adjusted_winrate, pf, sharpe), RegimeStats
    ✅ core/learning/winrate.py — WinRateTracker (record, query per strategy/regime, top_strategies, winrate_table)
    ✅ core/learning/weight_updater.py — WeightUpdater (target=base × wr/target, smoothing α, min/max weight)
    ✅ core/learning/engine.py — LearningEngine facade (record_result, update_weights, winrate_table, top)
    ✅ Wiring в run.py (shadow=True, min_trades=10)
    ✅ Tests: WinRateEntry, StrategyStats, WinRateTracker, WeightUpdater (strat_a ↑, strat_b ↓), LearningEngine ✓
```

---

## 16. Расширения, отложенные до v4 (после завершения миграции)

Идеи, требующие новых движков или значительного расширения существующих. Не включаются в v3, чтобы не затягивать миграцию.

| Идея | Что нужно | Статус в v3 |
|------|-----------|-------------|
| **Probability Engine** | Статистическая модель на основе истории сигналов (ML/байесовский) | Заложить интерфейс `ProbabilityEstimator`; реализация — после 500+ сделок |
| **Expected Move Engine** | Ретроспектива движения после каждого сигнала: средний %, время, макс | LearningEngine уже собирает WinRate; добавить сбор avg_move, avg_time |
| **Confidence Drift** | Пересчёт confidence после отправки сигнала (через 5/15/30 мин) | Заложить API `signal.update(id, new_confidence)`; механизм — v4 |
| **Market Memory** | Векторное хранилище (FAISS) + поиск похожих ситуаций по паттернам | FeatureStore сохраняет тензоры признаков для будущего анализа |
| **Market Graph** | Граф корреляций + распространение импульса по секторам | Отдельная версия v4 — ломает изоляцию per-symbol |

**GUI / расширения:**
- Web dashboard с живыми графиками (SigL, сделки, баланс)
- Экспорт (CSV, Telegram-channel, TradingView webhook)
- Mobile push (через Telegram — уже есть, но можно API)

---

## 17. Итого: что изменилось в V3

| Аспект | V1 плана | V3 (текущий) |
|--------|----------|--------------|
| Сроки | ~20 дней | 14–20 недель |
| V1→V2 переключение | «100% совпадение» | Feature flags: shadow→compare→active→retired с tolerance_pct |
| Risk Engine | cooldown | spread + ATR + liquidity + volatility + session |
| **OME** | ❌ отсутствует | ✅ PositionSizer, RiskManager, Tracker, Executor |
| Context Engine | сам считает | фасад — только агрегирует |
| Storage | in-memory | dual-write + ETL migration |
| Learning | общий | sliding window + hourly update + rollback |
| Multi-symbol | ❌ не учтено | per-symbol dict + asyncio.gather + semaphore |
| Update frequency | ❌ не указана | per engine таблица |
| Data flow | текстовая схема | детальный поток per tick |
| Тестирование | manual | unit + replay regression + shadow |
| Документация | ❌ | ADR + docs/ + воркшопы |
