# ADR-001: Отказ от scanner в пользу core/storage

**Дата:** 2026-07-12
**Статус:** ✅ Выполнено
**Влияет на версию:** v0.5.0

## Контекст

В текущей архитектуре `scanner/` является источником потоковых данных для V1-сигналов, API (`api/__init__.py`) и основного цикла (`run.py`). В V2-архитектуре, описанной в `ARCHITECTURE.md`, данные должны храниться в едином слое `core/storage/` (CandleStore, TickerStore, OBStore, TradeStore, LiquidationStore, WhaleTracker, FeatureStore).

## Решение

- Создать `core/storage/` со следующими модулями:
  - `candle_store.py` — замена `scanner.candles.CandleBuffer`
  - `ticker_store.py` — замена `scanner.ticker.TickerStore`
  - `ob_store.py` — замена `scanner.orderbook.OrderBookState` / `orderbooks`
  - `trade_store.py` — замена старого TradeStore
  - `liquidation_store.py` — замена `scanner.ticker.LiquidationStore`
  - `whale_tracker.py` — замена `scanner.trades.WhaleTracker`
- Мигрировать всех потребителей.
- Использовать Strangler Fig (данные пишутся параллельно в scanner stores и core.storage).
- Удалить scanner stores (оставив scanner classes для WS-обработки).

## Результат миграции (v0.5.0)

| # | Шаг | Статус | Комментарий |
|---|-----|--------|-------------|
| 1 | Создать `core/storage/` | ✅ | candle_store, ticker_store, ob_store, trade_store, liquidation_store, whale_tracker, feature_store |
| 2 | Мигрировать `run.py` | ✅ | 0 scanner store references; использует core.storage синглтоны |
| 3 | Мигрировать `signals/engine.py` | ✅ | Использует `get_candle_store()`, `get_ticker_store()`, `get_ob_store()`, `get_liquidation_store()`, `get_whale_tracker()` |
| 4 | Мигрировать `api/__init__.py` | ✅ | `/pairs` → core.storage; `/whales` → core.storage |
| 5 | Мигрировать `smoke_test.py` | ✅ | Все обращения к scanner заменены на core.storage |
| 6 | Обновить `tests/test_imports.py` | ✅ | Импорты из core.storage вместо scanner stores |
| 7 | Strangler Fig | ✅ | Scanner classes пишут данные и в старые буферы, и в core.storage |
| 8 | Тесты | ✅ | 33/33 passed (storage + imports) |

## Осталось

- `scanner/` не удалён целиком — scanner classes (CandleScanner, TickerScanner, LiquidationScanner, TradeScanner, OrderBookScanner, VolumeScreener) всё ещё нужны для WS-обработки в `run.py`.
- Когда V1 SignalEngine будет полностью замещён V2 Pipeline (core/features/calculators/), scanner classes можно будет удалить.

## Компромиссы

- Sync/Async dual-mode в хранилищах (get_sync для синхронных движков, get/put для асинхронных) — осознанное решение для совместимости.
- Scanner classes остаются в `scanner/` до полного перехода на core/features pipeline.
- `WhaleTracker` и `LiquidationStore` перенесены в core.storage, хотя по сути являются аналитическими, а не чисто storage-компонентами.

## Связанные ADR

- ADR-002: Глобальный FeatureEngine (вынос глобальных признаков)
