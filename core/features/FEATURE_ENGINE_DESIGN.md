# Feature Engine — Level 2 Архитектура

> **Единое место вычисления и кэширования признаков.**
> Каждый признак вычисляется **один раз**, кэшируется с TTL, и доступен всем стратегиям.
> Ни один сигнал больше не считает индикаторы самостоятельно.

---

## 1. Схема классов

```
┌──────────────────────────────────────────────────────────┐
│                    FeatureEngine                         │
│  ┌────────────────────────────────────────────────────┐  │
│  │  MarketDataBus subscription & event routing        │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐          │  │
│  │  │ WhaleCalc│  │ OHLCV    │  │ OrderBook│  ...     │  │
│  │  │          │  │ Calc     │  │ Calc     │          │  │
│  │  └────┬─────┘  └────┬─────┘  └────┬─────┘          │  │
│  │       │              │             │                 │  │
│  │       ▼              ▼             ▼                 │  │
│  │  ┌────────────────────────────────────────────────┐  │  │
│  │  │              FeatureStore                      │  │  │
│  │  │  ┌──────────┐  ┌──────────┐  ┌──────────┐      │  │  │
│  │  │  │ whale.*  │  │ ohlcv.*  │  │ ob.*     │ ...  │  │  │
│  │  │  │ TTL=5s   │  │ TTL=60s  │  │ TTL=1s   │      │  │  │
│  │  │  └──────────┘  └──────────┘  └──────────┘      │  │  │
│  │  └────────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  Public API для стратегий:                               │
│  ┌────────────────────────────────────────────────────┐  │
│  │ get_feature(symbol, name) → value                  │  │
│  │ get_multi(symbols, names) → {sym: {name: val}}     │  │
│  │ observe(symbol, name, callback)                     │  │
│  └────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

### 1.1 FeatureStore (`core/features/store.py`)

```python
class FeatureStore:
    """In-memory dict с TTL, thread-safe (asyncio.Lock), observer pattern."""

    # ── Single ──
    async get(symbol, name, default=None) -> Any
    async set(symbol, name, value, ttl=60.0)

    # ── Bulk ──
    async get_multi(symbols: list[str], names: list[str]) -> dict[str, dict[str, Any]]
    async set_multi(items: list[tuple[str, str, Any]], ttl=60.0)
    async get_by_pattern(name_prefix, symbol=None) -> dict[str, Any]

    # ── Observer ──
    observe(symbol, name, callback)
    observe_prefix(name_prefix, callback)
    unobserve(symbol, name, callback)

    # ── Management ──
    async evict(symbol, name=None)
    async clear()
    stats() -> dict
```

**Ключевые решения:**
- Разделитель `||` между символом и именем фичи (символы могут содержать `/` и `:`)
- `FeatureEntry` использует `__slots__` — минимальный overhead (4 атрибута)
- `asyncio.Lock` на запись, без блокировки на чтение (GIL-safe для dict)
- Observer'ы вызываются **после** записи, вне блокировки
- TTL = 0 означает «без TTL» (вечное хранение)

### 1.2 BaseFeatureCalculator (`core/features/base.py`)

```python
class BaseFeatureCalculator(ABC):
    event_channels: list[str]   # на какие каналы шины подписан
    feature_names: list[str]    # какие фичи производит
    default_ttl: float          # TTL по умолчанию
    priority: int               # порядок вычисления (меньше = раньше)

    @abstractmethod
    async def compute(symbol, event=None) -> dict[str, Any]:
        """Вернуть {feature_name: value, ...}."""

    async def on_event(event):   # вызывается FeatureEngine при событии
    async def compute_if_expired(symbol) -> bool:  # для forced refresh
```

**Ключевые решения:**
- `compute()` возвращает **словарь** фич — один калькулятор может производить несколько фич за один проход
- `on_event()` имеет встроенный TTL-контроль: не пересчитывает чаще, чем `default_ttl`
- `compute_if_expired()` вызывается FeatureEngine.get_feature(), когда фича протухла
- Калькуляторы сортируются по `priority` при диспетчеризации

### 1.3 FeatureEngine (`core/features/engine.py`)

```python
class FeatureEngine:
    register(calculator)
    register_many(calculators)
    get_calculator(name) -> BaseFeatureCalculator | None

    async start()    # подписка на MarketDataBus
    async stop()

    # ── API для стратегий ──
    async get_feature(symbol, name, auto_compute=True) -> Any
    async get_multi(symbols, names, auto_compute=True) -> dict
    async get_feature_raw(symbol, name) -> Any  # без auto_compute
    async get_by_pattern(name_prefix, symbol=None) -> dict
    async invalidate(symbol, name=None)

    # ── Observer (делегировано FeatureStore) ──
    observe(symbol, name, callback)
    observe_prefix(name_prefix, callback)

    @property
    stats() -> dict  # мониторинг
```

---

## 2. FeatureStore — интерфейс

### Публичные методы

| Метод | Описание | Сложность |
|-------|----------|-----------|
| `get(symbol, name, default=None)` | Одна фича | O(1) |
| `set(symbol, name, value, ttl)` | Записать фичу | O(1) |
| `get_multi(symbols, names)` | Несколько символов × фич | O(N×M) |
| `set_multi(items, ttl)` | Атомарная запись нескольких фич | O(N) |
| `get_by_pattern(name_prefix, symbol)` | Все фичи с префиксом | O(K) |
| `evict(symbol, name)` | Сбросить кэш | O(1) / O(K) |
| `observe(symbol, name, cb)` | Подписка на изменения | O(1) |
| `observe_prefix(name_prefix, cb)` | Подписка по префиксу | O(1) |
| `unobserve(...)` | Отписка | O(H) |
| `clear()` | Полная очистка | O(N) |
| `stats()` | Статистика | O(N) |

### Observer pattern

```python
# Подписка на конкретную фичу
store.observe("BTC/USDT:USDT", "whale.trades", my_callback)

# Подписка на все фичи с префиксом "whale"
store.observe_prefix("whale", my_callback)

# Колбэк: async def callback(symbol, feature_name, value)
```

---

## 3. Пример регистрации нового калькулятора

```python
from core.features.base import BaseFeatureCalculator
from core.features.engine import get_feature_engine

class MyCustomFeatureCalculator(BaseFeatureCalculator):
    """Считает уникальные признаки для моего сигнала."""

    event_channels = ["ticker.*", "candles.*"]
    feature_names = ["my_custom.rsi_divergence", "my_custom.signal_strength"]
    default_ttl = 30.0
    priority = 70

    async def compute(self, symbol: str, event=None) -> dict:
        # Берём готовые фичи из FeatureStore
        rsi = await self._store.get(symbol, "rsi.14")
        ema = await self._store.get(symbol, "ema.50")
        ticker = await self._store.get(symbol, "ticker.last")

        # Считаем что-то своё
        divergence = self._detect_divergence(rsi, ema)
        strength = self._calc_strength(ticker)

        return {
            "my_custom.rsi_divergence": divergence,
            "my_custom.signal_strength": strength,
        }

    def _detect_divergence(self, rsi, ema):
        # ... логика
        return {"exists": False}

    def _calc_strength(self, ticker):
        if not ticker:
            return 0.0
        return min(100, ticker.get("volume_24h", 0) / 1_000_000 * 10)


# Регистрация
engine = get_feature_engine()
engine.register(MyCustomFeatureCalculator())
await engine.start()
```

---

## 4. Интеграция с MarketDataBus

### Как это работает

```
  Binance WS       Bybit WS
     │                │
     ▼                ▼
  MarketDataBus (publish)
     │
     ├──→ Scanner (Ticker → TickerStore)
     ├──→ Scanner (Candles → CandleBuffer)
     ├──→ Scanner (Trades → WhaleTracker)
     ├──→ Scanner (OrderBook → OrderBookState)
     │
     └──→ FeatureEngine (подписка через bus.subscribe)
               │
               ├── WhaleCalculator  ← trades.*
               ├── OHLCVCalculator  ← candles.*
               ├── OrderBookCalc    ← orderbook.*
               ├── MarketCalculator ← ticker.*, liquidation.*
               │
               ▼
          FeatureStore
               │
               ▼
          SignalEngine._build_context()
               │
               ▼
          SignalContext(features=FeatureEngine)
               │
               ▼
          BaseSignal.check(ctx)
               │
               ├── ctx.features.get_feature("BTC", "rsi.14")
               ├── ctx.features.get_feature("BTC", "whale.trades")
               └── ctx.features.get_feature("BTC", "ob.imbalance")
```

### Подписка в `run.py`

```python
from core.features import get_feature_engine
from core.features.calculators.whale import WhaleFeatureCalculator
from core.features.calculators.ohlcv import OHLCVFeatureCalculator
from core.features.calculators.indicators import IndicatorsFeatureCalculator
from core.features.calculators.orderbook import OrderBookFeatureCalculator
from core.features.calculators.market import MarketFeatureCalculator
from core.features.calculators.volatility import VolatilityFeatureCalculator

# После создания bus, до запуска сигналов:
feature_engine = get_feature_engine()
feature_engine.register_many([
    WhaleFeatureCalculator(),
    OHLCVFeatureCalculator(),
    IndicatorsFeatureCalculator(),
    OrderBookFeatureCalculator(),
    MarketFeatureCalculator(),
    VolatilityFeatureCalculator(),
])
await feature_engine.start()

# FeatureEngine сам подписывается на нужные каналы bus
```

---

## 5. Миграционный путь: WhaleSignal → FeatureEngine

### Шаг 1. Создать FeatureCalculator (уже сделан)

`core/features/calculators/whale.py` — `WhaleFeatureCalculator`:
- Подписывается на `trades.*`
- Хранит буфер трейдов (deque, 20000 элементов)
- Вычисляет `whale.trades`, `whale.cvd`, `whale.aggression` каждые 5 секунд

### Шаг 2. Создать новую версию сигнала (уже сделан)

`signals/whale_v2.py` — `WhaleSignalV2`:
- Использует `ctx.features.get_feature(symbol, "whale.trades")` вместо `ctx.whale_trades`
- Имеет fallback на старый путь для обратной совместимости
- Может также брать дополнительные фичи: `whale.cvd`, `whale.aggression`

### Шаг 3. Включить в `run.py`

```python
# 1. Запустить FeatureEngine
feature_engine = get_feature_engine()
feature_engine.register_many([...])
await feature_engine.start()

# 2. Загрузить v2 сигналы (или заменить старые)
engine.load_signals(["whale_v2"])  # вместо "whale"
```

### Шаг 4. Выключить старый код

```python
# После полной миграции всех сигналов:
# 1. Удалить WhaleTracker из scanner/trades.py
# 2. Удалить whale_trades/cvd из SignalContext
# 3. Удалить старый whale.py
# 4. Переименовать whale_v2.py → whale.py
```

### Поэтапный план для всех ~50 сигналов

| Этап | Действие | Статус |
|------|----------|--------|
| 1 | FeatureStore + FeatureEngine + база калькуляторов | ✅ Готово |
| 2 | Whale → FeatureEngine (whale_v2) | ✅ Готово |
| 3 | OHLCV → FeatureEngine (ohlcv.*, vwap, atr) | ✅ Готово |
| 4 | Indicators → FeatureEngine (ema, rsi, adx, macd, bb) | ✅ Готово |
| 5 | OrderBook → FeatureEngine (spread, imbalance, walls) | ✅ Готово |
| 6 | Market → FeatureEngine (ticker, liquidation) | ✅ Готово |
| 7 | Volatility → FeatureEngine | ✅ Готово |
| 8-12 | Миграция сигналов: volume, smart_money, candle_technicals, trade_flow, hybrid | 📋 План |
| 13-20 | Миграция остальных сигналов на чтение из FeatureEngine | 📋 План |
| 21 | Чистка старых сканеров и прямых вычислений | 📋 План |

---

## 6. TTL по умолчанию

| Источник | TTL | Обоснование |
|----------|-----|-------------|
| trades (whale.*) | 5s | Трейды приходят каждую секунду |
| ticker.* | 5s | Тикер обновляется раз в 1-5 секунд |
| liq.* | 5s | Ликвидации — спорадические события |
| orderbook.* | 1s | Стакан может меняться каждые 100мс |
| ohlcv.* | 60s | Свечи 1m — раз в минуту |
| ema, rsi, macd | 60s | Пересчёт раз в минуту (на новых свечах) |
| vol.*, regime.* | 60s | Волатильность не меняется каждую секунду |

---

## 7. Ключевые решения

### Почему НЕ Redis?
- In-memory dict быстрее для однопроцессного приложения
- Нет лишнего сетевого round-trip
- Можно легко переключиться на Redis позже (FeatureStore — замена CacheBackend)

### Почему `||` как разделитель?
- Символы содержат `/` (BTC/USDT) и `:` (BTC/USDT:USDT)
- Ни один из стандартных разделителей не гарантирует уникальность
- `||` — маловероятен в тикере, легко читается

### Почему calculators - это классы, а не функции?
- Калькуляторы держат состояние (буферы трейдов, свечей)
- У каждого свой TTL, свои каналы подписки
- Легко тестировать изолированно

### Observer pattern — зачем?
- Стратегии могут подписаться на изменения фичи вместо polling
- Context Engine (Level 3) будет подписан на `vol.regime`, `regime.trend`
- Event Engine будет подписан на экстремальные значения
