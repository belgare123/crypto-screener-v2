# Полный архитектурный аудит Crypto Screener V2

**Дата:** 2026-07-12
**Версия base:** v0.6.0 (commit ce4ed96)
**Проанализировано:** 17,500 строк / 122 файла

---

## Структура проекта (общая)

| Подсистема | Строк | Файлов | Назначение |
|-----------|-------|--------|-----------|
| `core/` | 11,257 | 73 | V2 движки, хранилища, FeatureEngine, ML/обучение, OME |
| `signals/` | 3,877 | 35 | V1 сигналы + V1 SignalEngine + V1 Dispatcher |
| `alerts/` | 931 | 3 | TelegramNotifier, handlers, settings DB |
| `strategies/` | 690 | 3 | V2 стратегии (только momentum_v2) |
| `scanner/` | 613 | 6 | WebSocket подписки + устаревшие буферы |
| `context/` | 168 | 2 | ContextEngine, MarketContext |

---

## Модуль 1: Архитектура проекта

**Файлы:** `run.py`, `core/__init__.py`, `ARCHITECTURE.md`

### Общий поток

```
╔══════════════════════════════════════╗
║         run.py (818 строк)   ║
║     ┌─ всех 50 компонентов     ║
║     │                              ║
╚═══════╤══════════════════════════════╝
        │
        ▼
EventBus ←── WebSocket (7 scanner/)
  │
  ├──→ scanner/ (5 parallel pipelines: candles, ticker, trades, OB, volume)
  │       │
  │       ├──→ CandleBuffer, TickerStore, LiquidationStore (scanner/ — legacy)
  │       └──→ core/storage (Strangler Fig — same data written to both)
  │
  ├──→ V1 SignalEngine (35 signals) → V1 SignalQueue → V1 Dispatcher → Telegram
  │
  ├──→ V2 StateEngine (shadow) → Trend/Noise calc
  │       │
  │       └──→ FeatureEngine (partially) → only in test/development
  │
  ├──→ V2 FeatureEngine → FeatureStore (7 calculators, 54 features)
  │       │
  │       └──→ ContextEngine (reads from FeatureStore)
  │
  └──→ V2 StrategyEngine (1 strategy: momentum) → push_signal to V1
  │       │
  │       └──→ V1 SignalEngine (same queue!)
  │
  └──→ V2 SignalEngine (shadow) → does not send to Telegram
```

### Проблемы

**Проблема 1: V1/V2 Dual Pipeline.** Оба пайплайна работают параллельно и независимо. V1 — на данных из scanner/ (CandleBuffer, TickerStore legacy). V2 — на core/storage (CandleStore, TickerStore, OBStore). Фичи для торговли считаются дважды — в V1 сигналах и в V2 калькуляторах.

**Проблема 2: run.py — монолит.** 818 строк, 50+ шагов, ручное связывание компонентов. Нет механизма graceful shutdown. При падении любого компонента, все последующие шаги пропускаются.

**Проблема 3: Каскад синглтонов.** 12+ глобальных `get_*()` функций, создающих экземпляры в момент первого вызова, который может произойти в любое время — в конструкторе другого синглтона, в обработчике событий, в хендлере запроса. Порядок инициализации не гарантирован.

**Проблема 4: V2 VSHADE — Shadow-режим как глобальное состояние.** V2 StateEngine, V2 SignalEngine и V2 (core/storage) — все работают в shadow (режиме записи, но не влияния). Datas записываются, но не считываются V1 сигналами. V2 включится только через безболезненный фикс.

**Проблема 5: run.py не вызывает stop().** При перезапуске остаются висящие подписки в шине. При следующем запуске — дублирующиеся обработчики событий, многократный вызов pipeline'ов.

---

## Модуль 2: core/ — фундамент

**Файлы:** `core/__init__.py`, `core/state/engine.py`, `core/signal/engine.py`, `core/features/store.py`

### Проблемы

**Проблема 1: Каскад синглтонов через дефолтные конструкторы.** Каждый движок в конструкторе через `or` -цепочку тянет синглтоны других движков:

```python
class StateEngine:
    def __init__(self, bus=None, feature_store=None, ...):
        self.bus = bus or get_bus()
        self._store = feature_store or get_feature_store()
```

Создавая StateEngine() через неявный вызов get, ты получаешь целиком граф: МarketDataBus -> FeatureStore -> EventBus -> и весь базовый слой движков.

**Проблема 2: shadow как глобальное состояние.** Два компонента (StateEngine, V2 SignalEngine) по умолчанию инициализируются с `shadow=True`. Если `get_state_engine()` вызвали до run (например, в контексте автозагрузки), то run может получить не тот объект. При push-логе — дублирующий объект.

**Проблема 3: Флаги ютятся в _get_active symbols()** hардкодов 2 символа ("BTC/USDT:USDT", "ETH/USDT:USDT"). Подписка на 30 символов не используется.

**Проблема 4: Unity Threading.Lock корявый в asyncio-контексте.** Threading.Lock ставится в asyncio-задачах, что блокирует event loop. Только в core/signal/engine.py, но плохой прецендент.

**Проблема 5: FeatureStore.set_multi() — неатомарные нотификации.** Observers могут увидеть partial state — в хранилище только 2 set из 5.

**Проблема 6: V2 V ChangeWork  — синглтон get_engine() в V1 диалекте, дублирующий велосипед для каждого движка (все 12+ имеют одинаковую структуру).

**Проблема 7: Отсутствие мониторинга производительности.** Нет поддержки замеров, кеш-хитрейта, трейсинга pipeline. Проблема производительности может долго оставаться незамеченной.

---

## Модуль 3: scanner/ — остатки Strangler Fig

**Файлы:** scanner/ scanner/candles scanner/ticker scanner/orderbook scanner/trades, scanner/volume_screener, scanner/__init__.py

### Классификация

| Компонент | Строк | Тип | Статус | Действие |
|-----------|-------|-----|--------|----------|
| BaseScanner | 48 | ABC | ✅ Чистый | Останется: полехна абстракция |
| CandleBuffer | 50 | Store | 🗑️ Дубликат | Удалить — CandleStore в core/storage/ |
| CandleScanner | 40 | WS-handler | ✅ Нужен | Переименовать, но удалять не нужно |
| TickerStore (scanner/) | 40 | Store | 🗑️ Дубликат | Удалить — есть core/storage/ticker_store |
| LiquidationStore (scanner/) | 30 | Store | 🗑️ Дубликат | Удалить — есть core/storage/liquidation_store |
| TickerScanner | 40 | WS-handler | ✅ Нужен | — |
| LiquidationScanner | 40 | WS-handler | ✅ Нужен | —|
| TradeScanner | 40 | WS-handler | ✅ Нужен | — |
| OrderBookScanner | 41 | WS-handler | ✅ Нужен | — |
| VolumeScreener | 182 | REST-poll | ⚠️ Рефакторинг | Перевести на TickerStore |

### Проблемы

**Проблема 1: 4 legacy stores — мёртвый код под Strangler Fig.** CandleBuffer, TickerStore, LiquidationStore, WhaleTracker — точные копии того, что есть в core/storage/. Разница: нет TTL, нет asyncio, нет тестов. После v0.6.0 никто не ссылается.

**Проблема 2: VolumeScreener — чужеродный элемент.** 182 строки, не связан с EventBus. Имеет собственный асинхронный цикл, REST-клиент, дата-классы. Данные же уже есть в WebSocket (TickerStore).

**Проблема 3: No priority in BaseScanner.** Нельзя управлять порядком обработки.

**Проблема 4: TickerScanner не расширяет BaseScanner.** Он не вызывает `process(event)`. Работает через run.py по таймеру.

**Проблема 5: No stop() in run.py for scanners.** При перезапуске остаются дубли-каналы подписки в шине.

---

## Модуль 4: FeatureEngine

**Файлы:** core/features/ engine.py, store.py, base.py, calculators/ (7 файлов, 1542 строк)

### Состав

```
FeatureEngine (L2) — 285 строк
  │  ├── Event router: recruits handlers based on event_channels
  │  ├── Call registry: stores the list of calculators
  │  ├── Lifecycle: start/stop
  │  ├── API: get_feature, get_multi — with auto-compute
  │  └── Observer proxy: observe, observe_prefix
  │
  └── FeatureStore (L2+1) —331 строк
      ├── In-memory key-value store with TTL
      ├── Observer pattern
      └── Monitor (alloc counter)
```

### Проблемы

**Проблема 1: 6 ролей в одном классе.** 6 слитых ролей: Реестр (Registry), Жизненный цикл (Lifecycle), Маршрутизатор (wildcard - дублирует MyEventBop logic), Оркестратор (auto-compute in NxМ), Прокси-наблюдатель (для FeatureStore) и Статистика (stats). Смешение ответственностей.

**Проблема 2: get_multi() — N×M последовательные вычисления.** Для 100 фич × 20 символов = 200 итераций с асинхронным LOCK и проверкой TTL в каждой. Нет возможности batch. При холодном запуске это долбает 10+ секунд.

**Проблема 3: event_channels — дублирование wildcard-логики EventBus.** EventBus уже использует подобную логику. FeatureEngine пишет свою.

**Проблема 4: BaseFeatureCalculator. on_event() и compute_if_expired() копируют друг друга.** 85% кода идентично. Разница только в том, передается ли event в compute.

**Пробойма 5: Ни один V2 калькулятор ещё не в production.** V2 pipeline — в shadow: StateEngine не пишет в FeatureStore, V2 SignalEngine не отправляет сигналы. FeatureEngine работает, но его выход не потребляется.

**Проблема 6: default_ttl — TTL для всех фич одинаков.** 60 сек для RSI и Ema-200 (которым нужно 200 свечей) — бессмыслица.

**Проблема 7: Нет warmup.** При каждом старте FeatureEngine пуст. Первые 30-60 секунд любая попытка чтения фичи возвращает None.

---

## Модуль 5: ContextEngine

**Файлы:** context/market_context.py (160 строк)

### Данные

```python
MarketContext:
  trend: str          # "bull"/"bear"/"flat"
  volatility: str     # "low"/"normal"/"high"/"extreme"
  volatility_state: str # "expansion"/"compression"/"stable"
  regime_score: float  # 0-100
  session: str         # "asia"/"london"/"ny"
  session_momentum_weight: float
  threshold_multiplier: float
```

### Проблемы

**Проблема 1: Pull-based, не event-driven.** Каждый вызов get_context() = 4 последовательных запроса к FeatureStore. Нет подписки на изменения. Если за минуту вызван 10 раз, то FeatureStore тарифицируется по 40 запросам.

**Проблема 2: Нет кэширования.** Каждый вызов создает новый MarketContext с нуля. Session меняется раз в 5-7 часов, trend — раз в 5-60 минут.

**Проблема 3: Fallback on session with TWO levels.** Сначала SessionEngine, если он не передан — создастся грязные инстанс, если не сработает — детект вручную.

**Проблема 4: Строки вместо enum.** Сравнения `if ctx.trend == "bull"` разбросаны по коду. Если в одном месте написать "Bull" — молчаливая поломка.

**Проблема 5: Нет составных метрик.** Метод `_volatility_multiplier()` живет в стратегии, а не в контексте. Может быть использован другой.

**Проблема 6: Не подписана на изменения.** ContextEngine узнает о смене trend только когда кто-то вызовет get_context().

---

## Модуль 6: StrategyEngine (V2)

**Файлы:** strategies/ (3 файла, 690 строк, только 1 стратегия)

### Сравнение V1 vs V2

```
V1: 8 запросов к хранилищам (CandleBuffer + TickerStore + ...)
    + optional FeatureEngine
    + 35 сигналов последовательно

V2: 1 запрос к ContextEngine → FeatureEngine
    + 1 проверка (on_event → evaluate)
    + push_signal к V1 SignalEngine (да!)
```

### Проблемы

**Проблема 1: V2 не имеет собственного диспатчера.** V2 стратегия проталкивает результат через `push_signal()` обратно в V1 SignalEngine, который ставит в V1 очередь. V2 зависит от V1 для доставки.

**Проблема 2: Три разных уровня в конструкторе.** Конструктор принимает L2 (FeatureEngine), L3 (ContextEngine), V1 (SignalEngine), с двумя неявными singleton-дефолтами.

**Проблема 3: Если не передан SignalEngine, результаты V2 теряются.** Нет fallback, нет буфера.

**Проблема 4: Нет параллельности — стратегии последовательны.** Для 1 стратегии это нормально, но при добавлении >3-5 это катастрофа.

**Проблема 5: V2 не фильтрует события.** Любой event (тикер, ликвидации, стакан) триггерит УСЕ стратегии. Тикер приходит каждую секунду — 60 бесполезных вызовов в минуту.

**Проблема 6: push_signal — хрупкий и невалидированный API.** Передаются V2-имена (momentum_v2) как signal_name в V1 контекст.

**Проблема 7: Отсутствие V2 Dispatcher.** Стратегии проталкиваются через V1 Dispatcher который не знает V2-специфику.

**Проблема 8: Cooldown дублируется: V1 в SignalEngine + V1 в Dispatcher + V2 can_send().**

---

## Модуль 7: SignalEngine V1

**Файлы:** signals/ (35 файлов, 3877 строк, 35 сигналов)

### Состав сигналов

| Категория | Сигналы | Кол-во |
|-----------|---------|--------|
| CTA (candle techniques) | rsi, momentum, consecutive, volume_body, candle_pattern, inside_bar, pullback | 7 |
| Объем | volume_spike, trade_flow (3), sector (2) | 6 |
| Whale/СМ | whale, whale_v2, smart_money, cluster_buy | 5 |
| Обмен (orderbook) | orderbook, 4 orderbook_signals, hybrid (3) | 8 |
| Ликвидация | liquidation, 2 liquidation_advanced | 4 |
| Составные | ai_score, consensus_v2, indicator_v2 | 3 |
| Другие | market_breadth, market_leader, divergence, correlation, rotation, RS_strength | 7 |

### Проблемы

**Проблема 1: V1 полностью дублирует V2.** Оба пайплайна работают. V1: считает RSI в rsi_signal/indicator_v2/consensus V2: FeatureEngine считает rsi.14 кэширует. На каждую свечу два вычисления.

**Проблема 2: 8 синхронных вызовов CORE.storage на каждое событие.** V1 строит SignalContext всякий раз. На каждое событие это: CandleStore (1m, 5m, 15m), TickerStore, OBStore, WhaleTracker(2), LiquidationStore.

**Проблема 3: Два SignalCooldown на один pipeline.** Один в Engine._check_signal, другой в Dispatcher._dispatch_loop. Если 1 блокирует пропускает, 2 может еще блокировать или наоборот.

**Проблема 4: V1 считывает из scanner/ (CandleBuffer), не из core/storage.** После v0.6.0 CandleBuffer может получать неполные данные.

**Проблема 5: _signal_queue без порядка (FIFO).** Все сигналы равны в одной очереди: money whale (100 score) и какой-нибудь малозначительный.

**Проблема 6: V1 игнорирует V2-генерируемые данные.** StateEngine производит trend, volatility, сессию, но V1 о них не знает. Только события из дребезга (candles.* и trades.*) триггерят (не 5 генерации).

**Проблема 7: 2 подсчета метрик на одно и то же событие.**

---

## Модуль 8: Dispatcher

**Файл:** signals/dispatcher.py (117 строк)

### Pipeline

```
SignalEngine (V1) → asy138.SignalQueue → Dispatcher._dispatch_loop
  │                                               │
  │ 2x SignalCooldown                    SignalCooldown
  │                                               │
  └── StrategyEngine (V2) → push_signal           │
 (respetion endpoint)                              │
                                                  │
                                                  ▼
                                         TelegramNotifier
                                           send_signal / send_batch
```

### Проблемы

**Проблема 1: Два SignalCooldown с одинаковой конфигурацией.**
- Уровень 1: в SignalEngine (анти-дублирование) — _check_signal
- Уровень 2: в Dispatcher (антиспам) — _dispatch_loop

- Асинхронные — первый блокирует, второй проходит (или наоборот), создавая побочные недетерминированные эффекты.

**Проблема 2: Единый asyncio.Queue для всех источников.**
V1 (35 сигналов) и V2 (потенциально N фич) попадают в один и тот же нерегистрируемый канал.

**Проблема 3: Батчинг без логики категоризации.** Любые сигналы группируются — без разделения на важные и неважные. Whale-сигнал может потеряться между множеством маленьких, если их накопится много.

**Проблема 4: send_batch обрезает на 4000 символов — без сохранения значимости.** Обрезает все подряд; Whale-сигнал в конце — отбрасывается.

**Пробопаление: Dispatcher не восстанавливается любого раунда ошибку.** Нет backoff, retry, wait по retry-after.

**Проблема 6: send_batch vs send_signal_feed — дублирование.**

---

## Модуль 9: Telegram

**Файлы:** alerts/ (3 файла, 931 строка)

### Состав

| Файл | Строки | Назначение |
|------|--------|------------|
| telegram.py | 472 | TelegramNotifier + signal_to_text() |
| handlers.py | 361 | aiogram Router + inline keyboards |
| settings_db.py | 98 | UserSettingsDB (SQLite) |

### Проблемы

**Проблема 1: signal_to_text() — монолит-формат.** 30+ вложенных elif для каждого типа сигнала (whale, volume_spike, smart_money, ai_score, market_breadth, rsi_v2, orderbook, и т.д.). Каждый elif — дублирующая логика распаковки meta-полей. Править нужно один файл для каждого нового сигнала.

**Рекомендация:** SignalFormatters — регистрируемый паттер Visitor. Каждый сигнал регистрирует свой форматтер.

**Проблема 2: Telegram error handling — неполный.** Нет:
- Ожидания retry-after
- Обработки 'chat not found'
- Обработки 'bot blocked by user'
- exponential backoff при таймаутах

**Проблема 3: UserSettingsDB — singleton без close().** Новое соединение с БД создается при каждом вызове геттера. Нет WAL mode, нет пула.

**Проблема 4: Конфликт имён Dispatcher.** У aiogram тоже есть Dispatcher. В run.py импортируются одновременно несколько. Нужен алиас.

**Проблема 5: rate limit хардкод (0.3s).** send_batch() не делает паузу вообще; send_signal_feed() делает хардкод 0.3 секунд.

**Продлема 6: Inline-клавиатура без иерархичности.** Только русский текст, нет конфигурации / настройки как пасса.

---

## Модуль 10: Производительность

**Измерено:** сумма строк 17,500; 122 файла.

### Узкие места

| Узел | Оценка | Описание |
|------|--------|----------|
| get_multi NxM | 🔴 | O(N\M) с блокировкой асинхронных операторов |
| V1 35 последовательных | 🔴 | 280 операций на событие (8 вызовов + 35 сигналов) |
| 8x threading.Lock | 🔴 | 2 из которых (market_analysis, market_replay) I/O блокирующие |
| StateEvent — таймер, не событие | 🟡 | Данные живут до 60 секунд при пулле |
| VolumeScreener — REST вместо TTL | 🟡 | 200 ms вместо 5 ms |
| V1/V2 дублирование | 🟡 | Каждый индикатор считается дважды |

**Ожидаемое ускорение после v0.7.0:** 60-80% снижения загрузки ЦПУ за счет: 1) V1/V2 слияния 2) убирания 8 страниц 3) использования кэша.

---

## Модуль 11: Дорожная карта

### v0.7.0 — V1/V2 объединение + DI Container (HIGH priority)

**Цель:** V2 выходит из production shadow, V1 сокращается, run.py получает DI.

| # | Действие | Файлы | Влияние |
|---|----------|-------|---------|
| 1 | V1 Adapter: V1 читает ContextEngine вместо stack | signals/engine.py | 8 sync-запросов устраняются |
| 2 | V2 — direct dispatch | strategies/__init__.py, momentum_v2.py | V2 получает собственный голос |
| 3 | DI Container | core/di.py (new) + run.py rewrite | все синглтоны заменяются |
| 4 | FeatureEngine.warmup() | core/features/engine.py | Холодный старт устраняется |
| 5 | Удалить 4 legacy store | scanner/candles.py/scanner/ticker.py | -200 строк |
| 6 | VolumeScreener рефакторинг | scanner/volume_screener.py | REST -> in-memory |
| 7 | V1 Deprecated-тикеры | signals/candle_technicals.py | V1 начинает выдавать warning |
| 8 | Event фильтр в Strategy | strategies/__init__.py | тикеры/ликвидации больше не тригеррят V2 зря |
| 9 | asyncio.Lock | 2 файла | 0 нейронных блоков |

### v0.8.0 — FeatureSystem upgrade + ContextEngine reactive (MEDIUM)

**Цель:** FeatureEngine перестает быть богом. ContextEngine переходит на event-driven.

| # | Действие | Влияние |
|---|----------|---------|
| 1 | FeatureSystem: Registry + Cache + Composer + Provider | 6 ролей FeatureEngine -> 4 класса |
| 2 | ContextEngine cache | ~80% hits |
| 3 | Event-driven ContextEngine | Немедленная инвалидация (вместо up to 60s) |
| 4 | MarketContext enum'ы| Ошибка компиляции при опечатках |
| 5 | V2 Dispatcher (отдельный, с приоритетом) | V1 и V2 разделены |
| 6 | Единый SignalCooldown | один механизм антиспама |
| 7 | Гранулярный TTL | rsi: 60s, ema_200: 300s |
| 8 | Атомарные нотификации | set_multi = 1 notify |

### v0.9.0 — Decision Engine + Telegram stability (LOW priority)

**Цель:** система принимает решения, а не просто роняет по тикерной доске.

| # | Действие |
|---|----------|
| 1 | Decision Engine: Vote Collector + Opportunity Detector |
| 2 | SignalFormatter registry (patched) |
| 3| Telegram retry/backoff |
| 4| Telegram ratelimit — динамический |
| 5 | UserSettingsDB: WAL, close, pool |
| 6 | Мониторинг производительности (тайминги, кеш-хит, отказоустойчивость) |

### v0.10.0 — V2 only (NICE TO HAVE)

- V1 SignalEngine удален
- Все 35 сигналов мигрированы
- scanner/ — только WS-обработчики
- V2 Dispatch — единый (1 SignalQueue, 1 anti-spam, 1 набор метрик)

---

## Резюме: Ключевые действия сейчас

Требуется это:

1. **Создать ДИ контейнер** (core/di.py) — фундамент для всех остальных изменений.
2. **Удалить безболезненные мертвые классы**: CandleBuffer, TickerStore (scanner legacy), LiquidationStore (scanner), WhaleTracker (scanner).
3. **Адаптировать V1 на ContextEngine** — убрать 8 sync-вызовов.
4. **Stop-заглушить V1 signal cooldown дубликат** — оставить только в диспетчере.
5. **run.py сделать частями** — сваяй его тендер и retro-совместимость.
