# Phase 2: Strategy Engine (L4) + Context Engine (L3)

> **Цель:** Первая стратегия Momentum, которая заменяет 2 V1 сигнала (momentum + consecutive) и использует рыночный контекст. Dual-Run — V1 продолжает работать параллельно.

---

## 1. Архитектура

```
MarketDataBus
    │
    ├──→ V1 Signal Engine ─→ V1 Dispatcher ─→ Telegram
    │       (50 сигналов, не трогаем)
    │
    └──→ Feature Engine ─→ FeatureStore ─→ Context Engine (L3)
                                                │
                                                └──→ Strategy Engine (L4) ─→ V2 Dispatcher ─→ Telegram
                                                        │
                                                        ├── MomentumStrategy   ← новая
                                                        ├── VolumeStrategy     ← в будущем
                                                        ├── WhaleStrategy      ← в будущем
                                                        └── ...
```

### Принципы:
- **Dual-Run**: V1 сигналы не отключаем. Стратегия работает параллельно.
- **Add-only**: Ничего не удаляем, только добавляем новые файлы.
- **FeatureStore — единый источник**: Стратегия читает фичи, не трогает SignalContext.
- **Context — прослойка**: Стратегия не знает про сыые фичи, работает через MarketContext.

---

## 2. Файловая структура

```
crypto-screener-v2/
├── signals/              # V1 — без изменений
│   ├── base.py
│   ├── momentum.py       # ← V1, не трогаем
│   ├── candle_technicals.py  # ← V1 (содержит consecutive)
│   └── ...
│
├── strategies/           # NEW — L4 Strategy Engine
│   ├── __init__.py       # StrategyEngine, register_strategy, list_strategies
│   ├── base.py           # BaseStrategy, StrategyResult, StrategyContext
│   └── momentum_v2.py    # MomentumStrategy
│
├── context/              # NEW — L3 Context Engine
│   ├── __init__.py       # ContextEngine, get_context_engine
│   └── market_context.py # MarketContext — trend + vol + session
│
└── run.py                # MODIFIED — инициализация strategies + context
```

---

## 3. L3 — Context Engine (контекст рынка)

### Назначение
Предоставляет стратегиям единое представление "что сейчас происходит на рынке":
- Тренд (bull/bear/flat)
- Волатильность (low/normal/high/extreme)
- Сессия (Asia/London/NY/Overlap)
- Состояние волатильности (expansion/compression/stable)

### Компоненты

#### `context/__init__.py`
```python
# Re-export
from context.market_context import MarketContext, ContextEngine, get_context_engine
```

#### `context/market_context.py`
```python
class MarketContext:
    """Срез рыночного контекста для одного символа."""
    symbol: str
    trend: str           # bull / bear / flat
    volatility: str      # low / normal / high / extreme
    session: str         # asia / london / ny / overlap
    volatility_state: str # expansion / compression / stable
    regime_score: float  # 0-100

class ContextEngine:
    """Агрегирует данные из FeatureStore и core/session.py в MarketContext."""
    
    async def get_context(self, symbol: str) -> MarketContext:
        # 1. Читает фичи из FeatureStore: regime.trend, vol.regime, vol.regime_score, regime.volatility_state
        # 2. Определяет сессию через core/session.py
        # 3. Возвращает MarketContext
```

### Источники данных:
| Поле | Откуда | Feature |
|------|--------|---------|
| trend | VolatilityFeatureCalculator | `regime.trend` |
| volatility | VolatilityFeatureCalculator | `vol.regime` |
| regime_score | VolatilityFeatureCalculator | `vol.regime_score` |
| volatility_state | VolatilityFeatureCalculator | `regime.volatility_state` |
| session | `core/session.py` | — (функция) |

### Как контекст влияет на стратегию
```python
# Пример: в бычьем тренде снижаем порог для buy-сигналов
if context.trend == "bull":
    buy_threshold = 0.3  # легче получить buy
    sell_threshold = 0.7 # тяжелее получить sell
elif context.trend == "bear":
    buy_threshold = 0.7
    sell_threshold = 0.3
else:  # flat
    buy_threshold = 0.5
    sell_threshold = 0.5

# Волатильность влияет на порог размера движения
if context.volatility == "high":
    min_move_pct = 0.5  # больший порог в высокую волатильность
else:
    min_move_pct = 0.3
```

---

## 4. L4 — Strategy Engine

### `strategies/base.py`

```python
class StrategyResult:
    """Результат проверки стратегии — аналог SignalResult для стратегий."""
    strategy_name: str
    symbol: str
    direction: str        # buy / sell / neutral
    score: float          # 0-100
    confidence: str       # LOW / MEDIUM / HIGH
    factors: list[dict]   # [{"name": "momentum", "score": 30, "detail": "..."}, ...]
    context: MarketContext # какой был контекст при проверке
    meta: dict
    ts: float

class BaseStrategy(ABC):
    """Базовый класс стратегии."""

    meta: ClassVar[StrategyMeta]

    @abstractmethod
    async def evaluate(self, ctx: StrategyContext) -> StrategyResult | None:
        """Оценить стратегию. None = нет сигнала."""
        ...
```

### `strategies/__init__.py`

```python
# Реестр стратегий (аналог _signal_registry)
_strategy_registry: dict[str, type[BaseStrategy]] = {}

def register_strategy(name, description, category, ...):
    """Декоратор для регистрации стратегии."""

def list_strategies() -> dict[str, StrategyMeta]:
    """Список зарегистрированных стратегий."""

class StrategyEngine:
    """Движок стратегий — диспетчеризирует стратегии, управляет cooldown, отправляет в V2 Dispatcher."""
    
    def __init__(self, feature_engine, context_engine):
        ...
    
    async def on_event(self, event: Event):
        """На каждое событие проверяет все стратегии."""
    
    async def evaluate_all(self, symbol: str) -> list[StrategyResult]:
        """Проверить все стратегии для символа."""
```

---

## 5. L4 — MomentumStrategy (первая стратегия)

### Заменяемые V1 сигналы

| V1 сигнал | Файл | Логика |
|-----------|------|--------|
| **momentum** | `signals/momentum.py` | % change свечи > порог (0.3-0.5%) + направление |
| **consecutive** | `signals/candle_technicals.py` (ConsecutiveSignal) | N+ свечей одного цвета |

### Новая стратегия: `strategies/momentum_v2.py`

```python
class MomentumStrategy(BaseStrategy):
    """
    Momentum Strategy — комбинирует momentum + consecutive.
    
    Факторы:
    1. Momentum — резкое движение за свечу (% change > адаптивный порог)
    2. Consecutive — N+ свечей одного цвета
    3. Context — тренд, волатильность, сессия корректируют пороги и вес
    
    Score:
    - momentum(40) + consecutive(40) + context_bonus(20) = 0-100
    - Порог срабатывания: score >= 40
    - Cooldown: 180s (3 мин, быстрее V1 так как контекст меняется)
    """
    ...
```

### Детальная логика

#### Фактор 1: Momentum
```python
async def _factor_momentum(self, symbol: str, ctx: StrategyContext) -> dict | None:
    """Оценить момент. Использует 1m свечи из FeatureStore."""
    candles = await ctx.features.get_feature(symbol, "ohlcv.1m.buffer")
    if not candles or len(candles) < 3:
        return None
    
    last = candles[-1]
    prev = candles[-2]
    
    # % change
    change_pct = (last["close"] - prev["close"]) / prev["close"] * 100
    
    # Адаптивный порог от волатильности
    min_move = self._min_move_pct(ctx.context)
    
    if abs(change_pct) < min_move:
        return None
    
    score = min(40, abs(change_pct) / 0.5 * 40)  # 0.5% = 40 баллов
    direction = "buy" if change_pct > 0 else "sell"
    detail = f"{change_pct:+.2f}% (min={min_move}%)"
    
    return {"name": "momentum", "score": round(score, 1), "direction": direction, "detail": detail}
```

#### Фактор 2: Consecutive
```python
async def _factor_consecutive(self, symbol: str, ctx: StrategyContext) -> dict | None:
    """Счётчик последовательных свечей одного цвета."""
    candles = await ctx.features.get_feature(symbol, "ohlcv.1m.buffer")
    if not candles or len(candles) < 5:
        return None
    
    # Считаем сколько свечей подряд одного цвета
    count = 1
    direction = "neutral"
    for i in range(len(candles)-2, -1, -1):
        if candles[i+1]["close"] > candles[i+1]["open"]:
            curr = "buy"
        elif candles[i+1]["close"] < candles[i+1]["open"]:
            curr = "sell"
        else:
            break
        if direction == "neutral":
            direction = curr
        elif curr != direction:
            break
        count += 1
    
    if count < 2:
        return None
    
    # Усиление контекстом: в тренде consecutive весомее
    context_mult = self._context_multiplier(ctx.context, direction)
    score = min(40, (count - 1) * 10 * context_mult)
    detail = f"{count} consecutive {direction} candles"
    
    return {"name": "consecutive", "score": round(score, 1), "direction": direction, "detail": detail}
```

#### Фактор 3: Context Bonus
```python
def _context_factor(self, symbol: str, ctx: StrategyContext) -> dict:
    """Контекстный бонус: если momentum совпадает с трендом."""
    # Если momentum направлен по тренду → бонус
    trend = ctx.context.trend
    vol = ctx.context.volatility_state
    
    bonus = 0
    detail_parts = []
    
    if vol == "expansion":
        bonus += 5  # при расширении волатильности импульсы сильнее
        detail_parts.append("volatility expansion")
    
    return {"name": "context", "score": bonus, "detail": ", ".join(detail_parts) or "neutral"}
```

#### Итоговый расчёт
```python
async def evaluate(self, ctx: StrategyContext) -> StrategyResult | None:
    symbol = ctx.symbol
    
    # Факторы
    momentum = await self._factor_momentum(symbol, ctx)
    consecutive = await self._factor_consecutive(symbol, ctx)
    context_bonus = self._context_factor(symbol, ctx)
    
    factors = [f for f in [momentum, consecutive, context_bonus] if f is not None]
    
    if not factors:
        return None
    
    # Общий score (сумма, capped 100)
    total_score = sum(f["score"] for f in factors)
    total_score = min(100.0, total_score)
    
    if total_score < self.meta.min_score:
        return None
    
    # Направление: weighted vote by factor score
    buy_score = sum(f["score"] for f in factors if f.get("direction") == "buy")
    sell_score = sum(f["score"] for f in factors if f.get("direction") == "sell")
    
    if buy_score == sell_score:
        return None  # нейтрально
    
    direction = "buy" if buy_score > sell_score else "sell"
    confidence = self._classify_confidence(total_score)
    
    return StrategyResult(
        strategy_name=self.meta.name,
        symbol=symbol,
        direction=direction,
        score=total_score,
        confidence=confidence,
        factors=factors,
        context=ctx.context,
        meta={
            "momentum": momentum["detail"] if momentum else None,
            "consecutive": consecutive["detail"] if consecutive else None,
            "context_bonus": context_bonus["detail"],
        },
        ts=time.time(),
    )
```

---

## 6. Интеграция с run.py

### Добавить в `async def main()`:

```python
# ── Context Engine (L3) ──
from context import ContextEngine, get_context_engine
context_engine = ContextEngine(fe)

# ── Strategy Engine (L4) ──
from strategies import StrategyEngine
strategy_engine = StrategyEngine(fe, context_engine)

# Регистрируем стратегии
import strategies.momentum_v2  # noqa: F401 (self-registering via @register_strategy)
strategy_engine.register_all()

# Подписываем StrategyEngine на шину
bus.subscribe("candles.*", strategy_engine.on_event)
bus.subscribe("trades.*", strategy_engine.on_event)
```

### Health check:
```python
"strategies": len(strategy_engine.list_strategies()),
"v2_signals": sum(1 for s in list_signals().values() if "_v2" in s.name),
```

---

## 7. Стратегия vs Сигнал — разница

| Аспект | V1 Signal | V2 Strategy |
|--------|-----------|-------------|
| Источник данных | SignalContext (candles, trades, OB) | FeatureStore (кэшированные фичи) |
| Знания о рынке | Нет — только сырые данные | MarketContext (тренд, волатильность, сессия) |
| Результат | SignalResult (score + direction) | StrategyResult (score + factors + confidence + context) |
| Порог | Жёсткий (min_score=40) | Адаптивный (зависит от контекста) |
| Повтор | Cooldown + degrade mode | Cooldown + degrade + context change trigger |
| Объяснение | meta dict | factors (что дало score) + context (почему такой порог) |

---

## 8. Порядок имплементации (пошагово)

### Шаг 1: `context/market_context.py` + `context/__init__.py`
Создать ContextEngine с:
- Чтением фич из FeatureStore
- Определением сессии через `core/session.py`
- Адаптивными порогами

✅ **Верификация:** `python -c "from context import ContextEngine; ce = ContextEngine(fe); ctx = await ce.get_context('BTC/USDT:USDT'); print(ctx)"`

### Шаг 2: `strategies/base.py` + `strategies/__init__.py`
Создать BaseStrategy + StrategyEngine + registry:
- Декоратор `@register_strategy`
- StrategyEngine — управление жизненным циклом

✅ **Верификация:** `python -c "from strategies import list_strategies; print(list_strategies())"`

### Шаг 3: `strategies/momentum_v2.py`
Сама стратегия со всеми факторами.

✅ **Верификация:** `python -c "from strategies.momentum_v2 import MomentumStrategy; s = MomentumStrategy(); print(s.meta)"`

### Шаг 4: Интеграция в `run.py`
Подключить StrategyEngine к шине, добавить health check.

✅ **Верификация:** Запуск бота, проверка dispatch строк с `momentum_v2`

### Шаг 5: Dual-Run & мониторинг
- Стратегия логирует результат в `[strategy]` формате
- Сравниваем с V1 momentum + consecutive (логгируем оба)
- Расхождение < 10% = успех

---

## 9. Критические точки

### 🔴 Context Engine без фич
Если FeatureStore пуст (до warmup), ContextEngine возвращает `MarketContext.default()` — flat/neutral. Стратегия не выдаёт ложных сигналов.

### 🟡 Скорость
Стратегия делает 2 `get_feature()` вызова на символ. На 10 символах = 20 вызовов. FeatureEngine кэширует, так что это <5ms.

### 🟡 Совпадение с V1
Momentum V1 использует `candles[0]` (текущую, не закрытую) свечу. V2 стратегия должна использовать закрытые свечи из буфера. Возможны микросмещения. Решение: Dual-Run с логгированием расхождений.

### 🟢 Cooldown
Momentum V1: cooldown=300 (5мин). Стратегия: cooldown=180 (3мин) — быстрее за счёт контекстных изменений.
