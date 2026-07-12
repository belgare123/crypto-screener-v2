# ADR-001: Отказ от scanner в пользу core/storage

**Дата:** 2026-07-12
**Статус:** Принято
**Влияет на версию:** v0.6.0

## Контекст

В текущей архитектуре `scanner/` является источником потоковых данных для V1-сигналов, API (`api/__init__.py`) и основного цикла (`run.py`). В V2-архитектуре, описанной в `ARCHITECTURE.md`, данные должны храниться в едином слое `core/storage/` (CandleStore, TickerStore, OBStore, TradeStore, FeatureStore).

На момент v0.5.0 `scanner/` всё ещё используется:

| Файл | Импорт | Использование |
|---|---|---|
| `run.py` | CandleBuffer, TickerStore, —scanners (4 типа), VolumeScreener | Буферизация свечей, тикеров, OB, трейдов; запуск сканеров в основном цикле |
| `signals/engine.py` | candle_buffer, ticker_store, liquidation_store, whale_tracker, orderbooks | Data source для V1 SignalEngine |
| `api/__init__.py` | ticker_store, whale_tracker | Эндпоинты `/pairs`, `/whales/{symbol}` |
| `smoke_test.py` | Все сканеры | Интеграционный тест |
| `tests/test_imports.py` | scanner модули | Проверка импортов |

## Решение

- Оставить `scanner/` до v0.6.0 как временный слой совместимости.
- В v0.6.0 создать `core/storage/` со следующими модулями:
  - `candle_store.py` — замена `scanner.candles.CandleBuffer`
  - `ticker_store.py` — замена `scanner.ticker.TickerStore`
  - `ob_store.py` — замена `scanner.orderbook.OrderBookState`/`orderbooks`
  - `trade_store.py` — замена `scanner.trades.whale_tracker`
- Мигрировать всех потребителей.
- Удалить `scanner/` целиком.

## План миграции (v0.6.0)

1. Создать `core/storage/` с четырьмя модулями (candle, ticker, ob, trade).
2. Переписать `run.py` — заменить инициализацию scanner на core/storage.
3. Переписать `signals/engine.py` — импортировать из core/storage.
4. Переписать `api/__init__.py` — эндпоинты `/pairs`, `/whales`.
5. Удалить `scanner/` и все старые импорты.
6. Обновить `smoke_test.py` и `tests/test_imports.py`.
7. Проверить интеграцию через полный цикл запуска.

## Компромиссы

- Технический долг сохраняется до v0.6.0 (~2-3 часа работы).
- `scanner/` не мешает разработке V2 pipeline, но создаёт путаницу для новых разработчиков.
- API `/pairs` и `/whales` продолжают работать через scanner до миграции.

## Связанные ADR

- ADR-002: Глобальный FeatureEngine (вынос глобальных признаков)
