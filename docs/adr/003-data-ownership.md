# ADR-003: Владение данными (Data Ownership)

**Дата:** 2026-07-12
**Статус:** ✅ Принято
**Влияет на версию:** v0.7.0

## Контекст

В проекте 17,500 строк кода, данные проходят через 6 слоёв (WebSocket → scanner stores → V1 пайплайн, WebSocket → core/storage → FeatureEngine → ContextEngine → V2 пайплайн). Одна и та же сущность (candles, ticker, orderbook) может быть записана в 2 хранилища, прочитана 3 разными потребителями, и трансформирована 2 независимыми калькуляторами.

Это приводит к:

- **Расхождению данных**: V1 сигналы видят CandleBuffer, V2 видит CandleStore — они могут отличаться (один обновляется быстрее другого).
- **Дублированию вычислений**: два пайплайна считают RSI, EMA, MACD независимо.
- **Отсутствию ясной ответственности**: при ошибке в данных непонятно, чья вина — scanner, store или calculator.

Настоящий ADR фиксирует **владельцев данных** для каждого типа и **направление потока**.

## Решение

### Принципы

1. **Single Source of Truth (SSOT)**: каждый тип данных имеет ровно одно хранилище, которое считается источником истины.
2. **Write-once**: данные пишутся в хранилище один раз (WebSocket-обработчиком). Все остальные читают.
3. **Derived data in FeatureStore**: вычисляемые признаки (RSI, EMA, trend, volatility) живут в FeatureStore. Исходные данные (candles, ticker) — в соответствующих Store.
4. **Dependency direction**: данные текут только вниз по иерархии: Store → FeatureEngine → ContextEngine → Strategy/Signal. Никогда не наоборот.

### Матрица владения данными

| Тип данных | Хранилище (SSOT) | Пишет | Читает | Производные |
|-----------|-----------------|-------|--------|------------|
| Candles 1m | `CandleStore` | `CandleScanner` (WS) | V1 SignalEngine, OHLCVCalculator, IndicatorsCalculator, TrendStrength, MarketAnalysis | `candle.*`, `ohlcv.*` |
| Candles 5m/15m | `CandleStore` | `CandleScanner` (WS) | V1 SignalEngine | — |
| Ticker | `TickerStore` | `TickerScanner` (WS) | V1 SignalEngine, MarketFeatureCalculator, CorrelationEngine, RS/Rotation, Breadth, Heatmap, VolumeScreener | `market.*`, `global.*` |
| OrderBook | `OBStore` | `OrderBookScanner` (WS) | V1 SignalEngine, OrderBookCalculator, LiquidityZones | `ob.*`, `liquidity.*` |
| Trades | `TradeStore` | `TradeScanner` (WS) | WhaleTracker | — |
| Liquidations | `LiquidationStore` | `LiquidationScanner` (WS) | V1 SignalEngine | — |
| Whale Trades | `WhaleTracker` | `TradeScanner` (WS, derived) | V1 SignalEngine, WhaleCalculator | `whale.*` |
| CVD | `WhaleTracker` | `TradeScanner` (WS, derived) | V1 SignalEngine | — |
| RSI, EMA, MACD | `FeatureStore` | `IndicatorsFeatureCalculator` | V2 StrategyEngine, V1 IndicatorSignalV2 | — |
| Trend, Volatility | `FeatureStore` | `StateEngine` | ContextEngine → V2 StrategyEngine | — |
| Regime | `FeatureStore` | `StateEngine` | ContextEngine → V2 StrategyEngine | — |
| Session | `SessionEngine` | `SessionEngine.tick()` (таймер) | ContextEngine, MarketAnalysis | — |
| MarketContext | *transient* | `ContextEngine.get_context()` | V2 StrategyEngine | — |
| SignalResult | *transient* | V1 SignalEngine / V2 StrategyEngine | Dispatcher → TelegramNotifier | — |

### Устаревшие источники (будут удалены)

| Тип данных | Старое хранилище | Статус | Причина |
|-----------|-----------------|--------|---------|
| Candles | `scanner.candles.CandleBuffer` | 🗑️ Удалён (v0.6.0) | Дубликат CandleStore |
| Ticker | `scanner.ticker.TickerStore` | 🗑️ Удалён (v0.6.0) | Дубликат TickerStore |
| Liquidations | `scanner.ticker.LiquidationStore` | 🗑️ Удалён (v0.6.0) | Дубликат LiquidationStore |
| Whales | `scanner.trades.WhaleTracker` | 🗑️ Удалён (v0.6.0) | Дубликат WhaleTracker |

### Поток данных (целевой, v0.7.0+)

```
WebSocket
  │
  ├──→ scanner/ (CandleScanner, TickerScanner, etc.)
  │       │
  │       └──→ core/storage/* (SSOT)
  │               │
  │               ├──→ V1 SignalEngine (через V1ContextAdapter)
  │               │       │
  │               │       └──→ Dispatcher → Telegram
  │               │
  │               ├──→ FeatureEngine (calculators)
  │               │       │
  │               │       └──→ FeatureStore (derived data)
  │               │               │
  │               │               ├──→ ContextEngine
  │               │               │       │
  │               │               │       └──→ V2 StrategyEngine
  │               │               │
  │               │               └──→ V1 signals (через ctx.features)
  │               │
  │               └──→ StateEngine (таймер)
  │                       │
  │                       └──→ FeatureStore (regime.*)
  │
  └──→ VolumeScreener (чтение из TickerStore, не REST!)
```

## Компромиссы

- **V1 продолжает читать из core.storage** (а не из scanner/) — это уже сделано в v0.6.0. Разницы в данных между V1 и V2 больше нет на уровне хранилищ.
- **Разница остаётся на уровне вычислений**: V1 считает RSI в сигнале, V2 — в калькуляторе. Это устраняется в v0.7.0 через адаптер (V1 начинает читать фичи из FeatureStore).
- **SessionEngine** остаётся отдельным компонентом (не Store), так как это вычисляемый признак, а не поток данных.

## Связанные ADR

- ADR-001: Отказ от scanner в пользу core/storage (v0.5.0–v0.6.0)
- ADR-002: Глобальный FeatureEngine (GlobalFeatureService) — запланировано
