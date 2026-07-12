# ADR-002: Глобальный FeatureEngine (GlobalFeatureService)

**Дата:** 2026-07-12
**Статус:** Запланировано
**Целевая версия:** v0.6.0 (опционально) / v0.7.0

## Контекст

В V2 pipeline FeatureEngine вычисляет признаки для каждого символа независимо в циклах по `calculators`. Большинство калькуляторов (OHLCV, Indicators, OrderBook, Volatility) работают на одном символе и не требуют глобального контекста. Однако:

- **MarketFeatureCalculator** (`core/features/calculators/market.py`) — вычисляет доминацию BTC, корреляцию между символами, ширину рынка. Эти признаки требуют данных ПО ВСЕМ символам одновременно.
- **CorrelationEngine** (`core/correlation.py`) — уже существует отдельно, но не интегрирован в FeatureEngine.
- **RS / Rotation** (`core/rotation.py`, `core/relative_strength.py`) — используют только ticker_store, не FeatureStore.

Текущая архитектура не позволяет FeatureEngine вычислять глобальные признаки эффективно, так как `compute(symbol)` вызывается по одному символу.

## Решение

Вынести глобальные признаки в отдельный сервис — **GlobalFeatureService** — который:

1. Работает по расписанию (раз в 60 секунд), а не по событию.
2. Собирает данные по ВСЕМ отслеживаемым символам.
3. Вычисляет:
   - Доминацию BTC (BTC dominance = BTC объём / суммарный объём)
   - Попарную корреляцию (returns correlation matrix)
   - Ширину рынка (breadth — % монет выше SMA)
   - Относительную силу (RS ranking)
4. Сохраняет результаты в FeatureStore с префиксом `global.` (например, `global.btc_dominance`, `global.correlation.BTC-ETH`).
5. Стратегии получают глобальные признаки через `ctx.get_global("btc_dominance")`.

## Архитектура

```
┌─────────────────────┐
│ GlobalFeatureService │ ← по таймеру, раз в 60с
│  - collect_all()    │
│  - compute_global() │
│  - store_results()  │
└────────┬────────────┘
         │ запись
         ▼
┌─────────────────────┐
│    FeatureStore     │ ← prefix: global.*
│  global.btc_dom     │
│  global.corr.*      │
│  global.breadth     │
└─────────────────────┘
         │ чтение
         ▼
┌─────────────────────┐
│  StrategyContext    │
│  ctx.get_global()   │
└─────────────────────┘
```

## Компромиссы

- Дополнительная нагрузка: полный проход по всем символам раз в минуту (при 50 символах — незначительно).
- GlobalFeatureService должен ожидать готовности ticker_store (race condition при старте).
- FeatureStore нуждается в поддержке ключей без привязки к символу — либо глобальный namespace.

## Связанные ADR

- ADR-001: Отказ от scanner в пользу core/storage
