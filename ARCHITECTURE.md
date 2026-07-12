# Crypto Screener v2 — Архитектура и документация

## 0. Ключевые изменения

| Изменение | Файл | Описание |
|-----------|------|----------|
| **Phased Bootstrap** | `bootstrap.py` | Замена монолитного `run.py` на Container + фазы. Управляемый жизненный цикл, явные зависимости, graceful shutdown |
| **DI Container** | `core/di.py` | `Container` — регистрация/resolve компонентов. `Phase` enum. `run_phase()` с таймингом |
| **V2 Migration (v0.10.0)** | — | V1 SignalEngine, Dispatcher, V1 сигналы удалены. V2 pipeline в active mode |
| **Data Ownership** | `docs/adr/003-data-ownership.md` | ADR-003: матрица владения данными, SSOT, поток данных |

**Entry point:** `python bootstrap.py` (новый, рекомендуемый) или `python run.py` (старый, совместимость).

## 1. Общая архитектура

```
┌─────────────────────────────────────────────────────────────┐
│  Bybit WebSocket (v5 /public/linear)                         │
│  Стримы: kline.{tf}, publicTrade, tickers, allLiquidation,  │
│          orderbook.{depth}.200ms                             │
└────────────────────┬────────────────────────────────────────┘
                     │ Raw WS messages
                     ▼
┌─────────────────────────────────────────────────────────────┐
│  BybitExchange (exchanges/bybit/__init__.py)                │
│  • Декодирует WS → Event(channel, symbol, data, ts)        │
│  • Публикует в MarketDataBus                                │
│  • Auto-reconnect при разрыве                               │
└────────────────────┬────────────────────────────────────────┘
                     │ Event (на шине данных)
                     ▼
┌──────────────────────────────────────────────────────────────┐
│                    MarketDataBus (core/__init__.py)           │
│  Publish‑Subscribe шина — все компоненты общаются через неё  │
│  Подписки: 'candles.*', 'trades.*', 'ticker.*', '*'         │
└──┬───────────────┬──────────────┬───────────────┬───────────┘
   │               │              │               │
   ▼               ▼              ▼               ▼
┌─────────┐ ┌─────────┐ ┌───────────┐ ┌─────────────┐
│CandleScan│ │TradeScan│ │TickerScan │ │LiquidationSc│
│scanner/  │ │scanner/ │ │scanner/   │ │scanner/     │
│candles.py│ │trades.py│ │ticker.py  │ │ticker.py    │
└────┬─────┘ └────┬─────┘ └─────┬─────┘ └──────┬──────┘
     │            │              │              │
     ▼            ▼              ▼              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  FeatureEngine (core/features/engine.py)                            │
│  • 6+ calculators: whale, ohlcv, indicators, orderbook, market,    │
│    volatility                                                      │
│  • BATCH_UPDATER — pull-модель (каждые 5s) для всех пар            │
│  • stale-while-revalidate через get_stale() + peek()               │
└──────────────────────────────┬─────────────────────────────────────┘
                               │ context dict
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ContextEngine (context/engine.py)                                  │
│  • Реактивное обогащение контекста                                  │
│  • Lazy fetch через get_feature() + get_metric()                   │
│  • Ручной TTL на каждый ключ                                       │
└──────────────────────────────┬─────────────────────────────────────┘
                               │ StrategyContext
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  StrategyEngine (strategies/__init__.py)                            │
│  • 5+ V2 стратегий (Momentum, Whale, RS, SMA, OB)                  │
│  • @register_strategy декоратор                                    │
│  • Cooldown per strategy (30–300s)                                  │
│  • Отправляет SignalResult напрямую в TelegramNotifier             │
└──────────┬──────────────────────────────────────────────────────────┘
           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  TelegramNotifier — send_signal(sig)                                │
│  • Notify слушателей (SignalRecorder, метрики)                     │
│  • Retry/backoff при ошибках сети                                   │
│  • Сохраняет recent_signals для API                                 │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                    ┌──────┴──────┐
                    ▼              ▼
               ┌────────┐  ┌──────────────┐
               │Telegram│  │ SignalRecor. │
               │Бот     │  │ (SQLite)     │
               │@ecrv3  │  └──────┬───────┘
               │_bot    │         ▼
               └────────┘  ┌──────────────┐
                          │ WinRateCheck │
                          │ (5 min оценка│
                          │  win/loss)   │
                          └──────────────┘
```

## 2. Структура директорий

```
crypto-screener-v2/
│
├── run.py                      # Entry‑point — инициализация и запуск
├── config/__init__.py          # Pydantic‑настройки (env → CS_*)
├── core/                       # Ядро: шина, движки, трекеры
│   ├── __init__.py             # Event, SignalResult, MarketDataBus
│   ├── adaptive.py             # Adaptive Thresholds + VolatilityTracker
│   ├── session.py              # Session Engine (Asia/London/NY/Overlap)
│   ├── correlation.py          # Correlation Engine (Пирсон, лидеры, z‑score)
│   ├── relative_strength.py    # RS Engine (ratio к BTC, momentum)
│   ├── sector_scanner.py       # Sector Scanner (6 секторов)
│   ├── cache.py                # Кеш
│   ├── scheduler.py            # Планировщик
│   ├── worker.py               # Воркер пул
│   └── storage/                # Единый слой данных (v2, заменил scanner/*)
│       ├── candle_store.py     # CandleStore — MTF свечи
│       ├── ticker_store.py     # TickerStore — последние тикеры
│       ├── ob_store.py         # OBStore — стаканы + OrderBookState
│       ├── trade_store.py      # TradeStore — трейды
│       ├── liquidation_store.py # LiquidationStore — ликвидации
│       ├── whale_tracker.py    # WhaleTracker — киты + CVD
│       └── feature_store.py    # FeatureStore — кэш признаков
│
├── exchanges/                  # Адаптеры бирж
│   ├── bybit/__init__.py       # Bybit WS (sub/unsub/listen/reconnect)
│   ├── binance/__init__.py     # Binance (заготовка)
│   └── okx/__init__.py         # OKX (заготовка)
│
├── scanner/                    # Сканеры — разбирают WS данные
│   ├── candles.py              # CandleScanner + CandleBuffer (MTF)
│   ├── trades.py               # TradeScanner + WhaleTracker
│   ├── ticker.py               # TickerScanner + TickerStore (partial update) + LiquidationStore
│   └── orderbook.py            # OrderBookScanner
│
├── strategies/                  # V2 стратегии (заменили signals/ в v0.10.0)
│   ├── __init__.py              # StrategyEngine + @register_strategy
│   ├── base.py                  # BaseStrategy, StrategyContext, StrategyMeta
│   ├── momentum_v2.py           # V2 Momentum (+RS, SMA, Whale, OB, AI)
│   └── ...                      # Другие V2 стратегии
│
├── core/signal/                 # V2 SignalEngine (central dispatch)
│   └── engine.py                # SignalEngine — shadow=False (active since v0.10.0)
│
├── alerts/                     # Оповещения
│   ├── telegram.py             # TelegramNotifier (aiogram + SOCKS5)
│   ├── handlers.py             # Telegram handlers (меню, кнопки, настройки)
│   └── settings_db.py          # UserSettingsDB (SQLite per‑user)
│
├── storage/                    # Персистентность
│   ├── db.py                   # SignalDB (SQLite + aiosqlite)
│   └── analytics.py            # SignalRecorder + WinRateChecker + StatsReporter
│
├── utils/                      # Утилиты
│   ├── logger.py               # Логирование (цветной лог, rotation)
│   └── __init__.py             # SignalCooldown, helpers
│
├── database/                   # SQLAlchemy модели
│   ├── __init__.py             # init_database()
│   └── models.py               # ORM модели
│
├── data/                       # Данные (runtime)
│   ├── screener.db             # БД скринера
│   └── user_settings.db        # Настройки пользователей
│
├── api/                        # FastAPI (заготовка)
├── backtest/                   # Режим replay/бэктест (заготовка)
├── ranking/                    # Рейтинг (заготовка)
└── tests/                      # Тесты
```

## 3. Data Flow (полный цикл)

### Пошагово:

#### 3.1 Приём данных
**BybitExchange** подключается к `wss://stream.bybit.com/v5/public/linear` и создаёт 1 WS‑соединение. Подписки:

| Канал | Символы | Таймфрейм/параметры |
|-------|---------|---------------------|
| `kline.1` | 10 токенов | 1-минутные свечи |
| `kline.5` | 10 токенов | 5-минутные свечи |
| `kline.15` | 10 токенов | 15-минутные свечи |
| `publicTrade` | 10 токенов | Все сделки |
| `tickers` | 10 токенов | Тикеры (24h stats, last price) |
| `allLiquidation` | Все | Все ликвидации |
| `orderbook.50.100ms` | BTC, ETH | Стакан глубины 50 |

Каждое сообщение превращается в `Event(channel, symbol, data, ts)` и публикуется в `MarketDataBus`.

#### 3.2 Сканеры
Каждый сканер подписан на свой канал шины и пишет данные в **два хранилища** (Strangler Fig):
- **CandleScanner** → `candles.{symbol}` — обновляет `CandleStore` (MTF: 1m, 5m, 15m)
- **TradeScanner** → `trades.{symbol}` — обновляет `TradeStore` + `WhaleTracker` (крупные сделки + CVD)
- **TickerScanner** → `ticker.{symbol}` — обновляет `TickerStore` (last_price, volume, изменения)
- **OrderBookScanner** → `orderbook.{symbol}` — обновляет `OBStore` (bid/ask, стены, спред)
- **LiquidationScanner** → `liquidation.{symbol}` — обновляет `LiquidationStore` (кластеры)

Все данные пишутся в `core/storage/` (синглтоны). Старые scanner-буферы (`CandleBuffer`, `TickerStore` из scanner) получают данные параллельно (Strangler Fig) и будут удалены в v0.6.0.

#### 3.3 V2 Pipeline (v0.10.0)

Сигналы проходят через три последовательных слоя:

1. **FeatureEngine** (`core/features/engine.py`) — pull-модель, каждые 5 секунд BATCH_UPDATER обновляет признаки всех пар. Stale-while-revalidate: `get_stale()` → `peek()` → `compute_batch()`.
2. **StrategyEngine** (`strategies/__init__.py`) — @register_strategy декоратор. Cooldown per strategy (30–300s). Результат → `SignalResult` → напрямую в `TelegramNotifier.send_signal()`.
3. **TelegramNotifier** (`alerts/telegram.py`) — форматирование + Telegram API (aiogram + SOCKS5). Notify слушателей (SignalRecorder → SQLite, метрики). Retry/backoff при ошибках.

Подробнее: `bootstrap.py` (фазы STRATEGY, SERVICES, TELEGRAM, RUN).

#### 3.4 Anti‑spam (V2)

- Cooldown per strategy (устанавливается в @register_strategy, 30–300s)
- Risk Engine `shadow=False` — пре-трейд фильтрация (SpreadRule, ATRRule, LiquidRule)
- `TelegramNotifier.send_signal()` — проверка на дубликаты через `_recent_signals` (LIFO, 20 шт)

## 4. Core Engines (Tier 1)

### 4.1 Adaptive Thresholds (`core/adaptive.py`)
Динамические пороги. Базовые константы умножаются на три множителя:
```
итог = base × vol_mult × session_mult × symbol_mult
```

- **vol_mult**: ATR‑based (low 1.4, normal 1.0, high 0.7, extreme 0.5)
- **session_mult**: Asia 1.3, London 1.0, NY 0.95, Overlap 0.85
- **symbol_mult**: BTC/ETH 1.0, SOL/XRP 0.95, DOGE/ADA 0.9, SUI 0.8

Используется в сигналах через `adaptive.get("volume_z", symbol="BTC/USDT:USDT")`.

### 4.2 Session Engine (`core/session.py`)
Определяет сессии по UTC:
| Сессия | Часы UTC | Множитель порога |
|--------|----------|-----------------|
| Asia | 00:00-08:00 | ×1.3 |
| Asia-London overlap | 08:00-09:00 | ×1.1 |
| London | 09:00-13:00 | ×1.0 |
| London-NY overlap | 13:00-17:00 | ×0.85 |
| New York | 17:00-22:00 | ×0.95 |

Каждый профиль сессии содержит `typical_volatility_pct`, `volume_share_pct`, `momentum_weight`, `reversal_probability`.
Проверка раз в 60 сек. При смене → логирует `Session change: Asia → London`.

### 4.3 Correlation Engine (`core/correlation.py`)
- Сэмплирует цены каждые 2 секунды (`tick(prices)`)
- Rolling window: `PriceSeries(maxlen=120)` = 10 минут
- Корреляция Пирсона за 8 баров (~16с)
- Иерархия: BTC → ETH → SOL → ALT (TIER_1 → TIER_2 → TIER_3)
- Выдаёт: Alignment (0-1), Leaders/Laggards, Divergences (z‑score > 2.0)
- Сигналы: `market_leader` (кто двинулся раньше), `divergence` (рассинхрон), `market_alignment`

### 4.4 Relative Strength Engine (`core/relative_strength.py`)
- RS = цена / цена_BTC
- Каждые 2 секунды обновляет RS для всех 10 монет
- RS Momentum: изменение RS за 5 и 15 сэмплов (≈ 10с и 30с)
- Ранжирование: топ-3 сильнейших/слабейших по `rs_5m_pct`
- Сигналы: `rs_momentum` (резкое RS ±5% за 5м), `rs_ranking` (топ-3 лидеров/аутсайдеров)

### 4.5 Sector Scanner (`core/sector_scanner.py`)
Сектора:
| Сектор | Монеты |
|--------|--------|
| L1 | BTC, ETH, SOL, ADA, AVAX |
| Meme | DOGE |
| DeFi | LINK |
| Infra | SUI |
| Payment | XRP |
| Ecosystem | DOT |

- Принимает ranking из RS Engine
- Среднее RS по сектору, лидер сектора
- `rotation_detected` — сменился лидирующий сектор
- Сигналы: `sector_rotation` (смена лидера), `sector_strength` (сильный/слабый сектор)

## 5. Telegram бот

### Компоненты:
- **`alerts/telegram.py`** — `TelegramNotifier`: отправка сигналов в Telegram канал
- **`alerts/handlers.py`** — aiogram Router: команды / инлайн‑меню
- **`alerts/settings_db.py`** — `UserSettingsDB`: SQLite с настройками по `chat_id`

### Команды Telegram:
| Команда | Действие |
|---------|----------|
| `/start` | Главное меню с кнопками |
| `/settings` | ⚙️ Настройки |
| `/menu` | Главное меню |
| `/help` | Справка |

### Инлайн-меню:
- **📊 Статус** — аптайм, кол-во сигналов, сессия, корреляция, RS топ, сектор, события
- **📈 RS-топ** — топ монет по Relative Strength
- **⚙️ Настройки** — биржа, рынок, порог, таймфрейм, min score (с выбором ✅)
- **📈 Статистика** — win rate, количество сигналов по типам
- **🔔 Сигналы** — последние 10 отправленных сигналов
- **💡 Помощь** — описание проекта

### SOCKS5:
Telegram работает через `socks5://127.0.0.1:10808` (Happ VPN).  
aiogram 3.29+ использует `AiohttpSession(proxy="socks5://127.0.0.1:10808")`.

## 6. Аналитика

### SignalRecorder
- Каждый отправленный сигнал записывается в SQLite (`signals.db`)
- Поля: `id, timestamp, signal_name, symbol, direction, score, price, exchange, meta`
- SignalDB (`storage/db.py`) с aiosqlite

### WinRateChecker
- Раз в 5 минут проверяет сигналы старше 30 минут
- Берёт текущую цену из TickerStore
- Для `buy`: win если цена выросла; для `sell`: win если упала
- Пишет результат в `signals.db` (evaluate win/loss)

### StatsReporter
- Раз в час шлёт сводку win rate в Telegram

## 7. Конфигурация

`.env` → `config/__init__.py` (Settings, префикс `CS_`):

| Параметр | По умолчанию | Описание |
|----------|-------------|----------|
| CS_EXCHANGES | ["bybit", "binance"] | Биржи |
| CS_TESTNET | false | Тестнет |
| CS_TELEGRAM_TOKEN | — | Токен бота |
| CS_TELEGRAM_CHAT_ID | — | ID канала |
| CS_DEFAULT_TIMEFRAME | 15m | Таймфрейм |
| CS_MAX_PAIRS | 50 | Пар в отслеживании |
| CS_MIN_VOLUME_USDT | 1,000,000 | Мин объём |
| CS_MIN_SIGNAL_SCORE | 60 | Мин score сигнала |
| CS_ANTISPAM_COOLDOWN | 1800 | Антиспам (30 мин) |
| CS_DATA_DIR | data/ | Директория данных |

## 8. Статус развития

### ✅ Tier 1 (Must Have) — ВСЁ РАБОТАЕТ
| # | Фича | Сигналы |
|---|------|---------|
| 1 | Adaptive Thresholds | Динамические пороги ATR × сессия × символ |
| 2 | Session Engine | Asia/London/NY/Overlap множители |
| 3 | Correlation Engine | market_leader, divergence, market_alignment |
| 4 | Relative Strength | rs_momentum, rs_ranking |
| 5 | Sector Scanner | sector_rotation, sector_strength |

**Итого:** 35 сигналов, 0 ошибок, Telegram подключён, SOCKS5 работает.

### ⏳ Tier 2 (В очереди)
| # | Фича |
|---|------|
| 6 | Anomaly Detection |
| 7 | Regime Detection |
| 8 | Pattern Matching |
| 9 | Supply & Demand |
| 10 | Volume Profile |

### 🔮 Tier 3 (Планы)
| # | Фича |
|---|------|
| 11 | Signal DNA |
| 12 | Pattern Similarity |
| 13 | AI Clustering |
| 14 | Signal Lifecycle |
| 15 | Confidence Drift |
| 16 | Expected Move |
| 17 | Risk Meter |
| 18 | Noise Filter |
| 19 | Market Replay |
| 20 | Explainable AI |

---

## Технический долг

### 1. Модуль `scanner/` (legacy)

**Статус:** ✅ Полностью мигрирован в `core/storage/`. Legacy stores удалены (v0.7.0).

`scanner/` теперь содержит только WS-обработчики (Scanners), которые пишут напрямую в `core/storage/`. Это полностью очищенный слой WebSocket-подписок без двойной записи.

**ADR:** [ADR-001: Отказ от scanner](docs/adr/001-scanner-deprecation.md)

**Что сделано (v0.7.0):**
- Удалены legacy stores: CandleBuffer, TickerStore, LiquidationStore, WhaleTracker, `orderbooks` dict
- Удалены все Strangler Fig дублирующие записи в scanner/файлах
- Все Scanner-классы пишут исключительно в `core/storage/`
- Импорты протестированы, 164 теста проходят

**Осталось:**
- [x] Удалить `scanner/volume_screener.py` (чужеродный модуль, P2) ✅
- [ ] Полностью удалить `scanner/` когда все компоненты переедут в core/

### 2. Модуль `signals/` (V1 legacy) — ВЫПОЛНЕНО (v0.10.0)

**Статус:** ✅ Удалён

V1 SignalEngine, Dispatcher и 35 V1 сигналов полностью удалены из кодовой базы. V2 pipeline (FeatureEngine → StrategyEngine → TelegramNotifier) работает в active mode.

### 3. Глобальные признаки (GlobalFeatureService)

**Статус:** 📋 Запланировано  
**ADR:** [ADR-002: Глобальный FeatureEngine](docs/adr/002-global-feature-engine.md)

`MarketFeatureCalculator` и `CorrelationEngine` требуют данных по всем символам, но FeatureEngine работает в цикле по одному символу. Решение — вынести в отдельный `GlobalFeatureService` с периодическим обновлением раз в 60 секунд.

### 4. Единый интерфейс Engine

**Статус:** 🟡 Косметическое улучшение

Не все движки наследуют `core/engine/Engine` (абстрактный базовый класс). Для единообразия стоит привести все 9 движков к единому интерфейсу (start/stop/shadow), но это не критично для функционирования.
