# Crypto Screener v2 — План миграции на ARCHITECTURE_V2

> **Дата:** 2026-07-11
> **Цель:** Перевести ~50 сигналов с монолитной архитектуры (BaseSignal + check()) на многоуровневую архитектуру (Feature Engine → Context Engine → Strategy Engine → Signal Engine → Consensus Engine).
> **Принцип:** Ничего не ломать во время миграции — оба движка работают параллельно.

---

## 1. Инвентаризация всех 50 сигналов

### Легенда столбцов
| Колонка | Значение |
|---------|----------|
| **check()** | Реальный код или заглушка (`None`) |
| **Роль** | Какую функцию выполняет в системе |
| **→ Уровень V2** | Куда переедет |
| **Выживет как** | Отдельная стратегия, часть стратегии, фича, или удалить |

### 1.1 Активные сигналы (реальный код в check())

| # | Имя сигнала | Категория | check() | Роль | → Уровень V2 | Выживет как |
|---|-------------|-----------|---------|------|-------------|-------------|
| 1 | **volume_spike** | volume | ✅ | Z-score объёма, адаптивный порог | **L2 Feature Engine** (Volume Profile) + **L4 Strategy Volume** | Часть стратегии Volume. Признак → Feature Engine |
| 2 | **whale** | whale | ✅ | Крупные сделки $100K+ | **L2 Feature Engine** (Whales) + **L4 Strategy Whale** | Часть стратегии Whale. Признак → Feature Engine |
| 3 | **smart_money** | smart_money | ✅ | Конфлюенция CVD+Vol+Whale+Liq+OB → smart money score | **L7 Consensus Engine** | Прообраз Consensus Engine. Переписать как голосовалку |
| 4 | **liquidation** | liquidation | ✅ | Аномальный объём ликвидаций | **L4 Strategy Liquidation** (подсигнал) | Часть стратегии Liquidation |
| 5 | **orderbook** | orderbook | ✅ | Дисбаланс стакана, стены | **L2 Feature Engine** + **L4 Strategy Orderbook** | Часть стратегии Orderbook |
| 6 | **rsi** | candle | ✅ | RSI(14) с адаптивными порогами | **L2 Feature Engine** (indicators) + **L4 Strategy Mean Reversion** | Признак → Feature Engine |
| 7 | **momentum** | candle | ✅ | Резкое движение за свечу | **L2 Feature Engine** (momentum) + **L4 Strategy Momentum** | Часть стратегии Momentum |
| 8 | **consecutive** | candle | ✅ | N+ свечей одного цвета | **L2 Feature Engine** (trend) + **L4 Strategy Trend** | Признак тренда |
| 9 | **volume_body** | candle | ✅ | Большое тело + аномальный объём | **L4 Strategy Volume/Breakout** | Подсигнал стратегии Volume |
| 10 | **candle_pattern** | candle | ✅ | Engulfing, Hammer, Doji, PinBar | **L4 Strategy Reversal** | Часть стратегии Reversal |
| 11 | **inside_bar** | candle | ✅ | Сужение диапазона | **L4 Strategy Breakout** (пред-сигнал) | Подсигнал Breakout |
| 12 | **pullback** | candle | ✅ | Откат в тренде | **L4 Strategy Trend** | Подсигнал Trend |
| 13 | **spread_widening** | orderbook | ✅ | Аномальный спред | **L2 Feature Engine** (liquidity) + **L6 Risk Engine** | Признак для Risk Engine |
| 14 | **depth_ratio** | orderbook | ✅ | Концентрация объёма в 5 уровнях | **L2 Feature Engine** (liquidity) | Признак |
| 15 | **wall_stacked** | orderbook | ✅ | Множественные стены | **L2 Feature Engine** (walls) + **L4 Strategy Orderbook** | Часть стратегии Orderbook |
| 16 | **iceberg** | orderbook | ✅ | Айсберг-ордера | **L2 Feature Engine** (iceberg detection) | Признак |
| 17 | **buy_sell_ratio** | trade_flow | ✅ | Соотношение buy/sell объёмов | **L2 Feature Engine** (aggression) + **L4 Strategy Whale** | Часть стратегии Whale |
| 18 | **taker_flow** | trade_flow | ✅ | Рыночные buy vs sell объём | **L2 Feature Engine** (CVD/Delta) + **L4 Strategy Volume** | Признак |
| 19 | **cluster_buy** | trade_flow | ✅ | 3+ китовых buy за 5 мин | **L2 Feature Engine** (whale cluster) + **L4 Strategy Whale** | Подсигнал Whale |
| 20 | **whale_accum** | trade_flow | ✅ | Дистрибуция/накопление китов | **L4 Strategy Whale** (продвинутый) | Специализированный подсигнал Whale |
| 21 | **liquidation_cascade** | liquidation | ✅ | N+ ликвидаций за 30с = каскад | **L2 Feature Engine** (liquidation) + **L4 Strategy Liquidation** | Подсигнал Liquidation |
| 22 | **squeeze_setup** | liquidation | ✅ | Long squeeze → отскок | **L4 Strategy Liquidation** (продвинутый) | Подсигнал Liquidation |
| 23 | **liquidation_cluster** | liquidation | ✅ | Скопление ликвидаций на цене | **L2 Feature Engine** (liquidation clusters) + **L4 Strategy Liquidation** | Признак + подсигнал |
| 24 | **wall_bounce** | hybrid | ✅ | Bid wall + sell liq = отскок | **L7 Consensus Engine** (комбинация) | Убрать — покроется комбинацией стратегий |
| 25 | **whale_wall** | hybrid | ✅ | Whale buy у ask-стены | **L7 Consensus Engine** | Убрать — покроется комбинацией |
| 26 | **cvd_divergence** | hybrid | ✅ | Цена вверх/CVD вниз = дивергенция | **L4 Strategy Reversal** | Часть стратегии Reversal |
| 27 | **bid_ask_ratchet** | hybrid | ✅ | Цена вверх/ask растёт = сопротивление | **L2 Feature Engine** (orderbook) + **L4 Strategy Orderbook** | Признак + подсигнал |
| 28 | **ai_score** | ai | ✅ | Агрегированный балл 6 факторов | **L7 Consensus Engine** (прототип) | Прообраз для переписывания |

### 1.2 Пассивные сигналы (check() = None, генерируются core-движками)

| # | Имя сигнала | Категория | Роль | → Уровень V2 | Выживет как |
|---|-------------|-----------|------|-------------|-------------|
| 29 | **market_breadth** | market_breadth | % зелёных монет | **L2 Feature Engine** (breadth) **+** **Market Pulse** | Уникальный модуль Market Pulse |
| 30 | **heatmap** | heatmap | Тепловая карта | **L10 UI** (дашборд) | Убрать из сигналов — чисто UI |
| 31 | **rotation** | rotation | Ротация капитала | **L4 Strategy Rotation** (новая) | Переписать как стратегию |
| 32 | **liquidity_zone** | liquidity | Зоны ликвидности | **L2 Feature Engine** (liquidity zones) | Признак |
| 33 | **trend_strength** | trend | Сила тренда | **L3 Context Engine** (trend context) | Переезжает в Context Engine |
| 34 | **signal_dna** | ml | Отпечаток сигнала | **L9 Intelligence Engine** | Переезжает в Market Memory/Intelligence |
| 35 | **pattern_similarity** | ml | Поиск похожих паттернов | **L9 Intelligence Engine** | Переезжает в Intelligence |
| 36 | **ai_cluster** | ml | ML кластеризация | **L9 Intelligence Engine** | Переезжает в Intelligence |
| 37 | **signal_lifecycle** | ml | Стадии сигнала Born→Dead | **L8 Learning Engine** (meta) | Переезжает в Learning Engine |
| 38 | **confidence_drift** | ml | Падение уверенности | **Time Decay** модуль | Переезжает в Time Decay |
| 39 | **expected_move** | analytics | Ожидаемое движение ±X% | **L3 Context Engine** (volatility) | Признак для Context Engine |
| 40 | **risk_meter** | analytics | Риск LOW/MEDIUM/HIGH | **L6 Risk Engine** | Переезжает в Risk Engine |
| 41 | **noise_filter** | analytics | Шум рынка X% — Skip | **L3 Context Engine** (market regime) | Переезжает в Context Engine |
| 42 | **market_replay** | analytics | Исторический снэпшот | **L9 Intelligence Engine** | Инфраструктура, не сигнал |
| 43 | **signal_explain** | analytics | Объяснение сигнала | **L7 Explain Engine** | Переезжает в Explain Engine |

### 1.3 Специфические сигналы из других модулей

| # | Имя сигнала | Модуль | Роль | → Уровень V2 | Выживет как |
|---|-------------|--------|------|-------------|-------------|
| 44 | **market_leader** | correlation | Альт ведёт рынок | **L4 Strategy Rotation** | Подсигнал Rotation |
| 45 | **divergence** | correlation | Рассинхронизация с рынком | **L4 Strategy Reversal** + **L2 Feature Engine** | Подсигнал Reversal |
| 46 | **market_alignment** | correlation | Общий настрой рынка | **L3 Context Engine** (sentiment) | Контекст, не сигнал |
| 47 | **rs_momentum** | relative_strength | Резкая смена RS | **L2 Feature Engine** (RS) + **L4 Strategy Rotation** | Признак |
| 48 | **rs_ranking** | relative_strength | Топ-3 по RS | **L2 Feature Engine** + **Opportunity Scanner** | Уникальный модуль Scanner |
| 49 | **sector_rotation** | sector | Смена лидирующего сектора | **L4 Strategy Rotation** | Часть стратегии Rotation |
| 50 | **sector_strength** | sector | Топ-3 секторов | **L2 Feature Engine** (sector RS) + **L10 UI** | Признак для Dashboard |

---

## 2. Маппинг текущих категорий → уровни V2

```
Текущая категория    →  Уровень V2
─────────────────────────────────────
volume                →  L2 Feature + L4 Strategy Volume
whale                 →  L2 Feature + L4 Strategy Whale
smart_money           →  L7 Consensus Engine (прототип)
liquidation           →  L2 Feature + L4 Strategy Liquidation
orderbook             →  L2 Feature + L4 Strategy Orderbook
candle                →  L2 Feature + L4 Strategy (Reversal/Breakout/Trend)
trade_flow            →  L2 Feature + L4 Strategy (Whale/Volume)
hybrid                →  L7 Consensus Engine (комбинации)
ai                    →  L7 Consensus Engine
correlation           →  L2 Feature + L4 Strategy Rotation
relative_strength     →  L2 Feature + Opportunity Scanner
sector                →  L2 Feature + L4 Strategy Rotation
market_breadth        →  Market Pulse (уникальный модуль)
heatmap               →  L10 UI Dashboard
rotation              →  L4 Strategy Rotation (новая)
liquidity             →  L2 Feature Engine
trend                 →  L3 Context Engine
ml                    →  L9 Intelligence Engine / L8 Learning Engine
analytics             →  L6 Risk Engine / L3 Context Engine / L7 Explain Engine
```

---

## 3. Порядок внедрения уровней (Roadmap)

### Фаза 0: Подготовка инфраструктуры (1-2 недели)
**Цель:** Ничего не сломать. Подготовить почву.

| Шаг | Что делаем | Сложность | Риски |
|-----|-----------|-----------|-------|
| 0.1 | Создать FeatureStore — общее in-memory кэш-хранилище признаков | ⭐ | Низкий — add-only |
| 0.2 | Создать параллельную шину событий V2 bus (не ломая старую) | ⭐⭐ | Конфликт имён событий |
| 0.3 | Добавить Feature Engine scaffold (пустые калькуляторы) | ⭐ | Низкий |
| 0.4 | Добавить `V2_ENABLED = False` флаг в конфиг | ⭐ | Обязательно |
| 0.5 | Написать адаптер: старая шина → FeatureStore → старая шина (bridge) | ⭐⭐⭐ | **Критично** — если bridge сломается, сигналы умрут |
| 0.6 | CI + тесты: запуск V1 и V2 параллельно, сравнение output | ⭐⭐ | Тесты должны быть исчерпывающими |

### Фаза 1: Feature Engine — L2 (2-3 недели)
**Цель:** Все признаки вычисляются один раз, кэшируются в FeatureStore.

| Шаг | Что делаем | Сложность | Риски |
|-----|-----------|-----------|-------|
| 1.1 | **Price/Volume**: OHLCV, VWAP, ATR, Volume Profile | ⭐ | Переписать core/adaptive.py → L2 |
| 1.2 | **Индикаторы**: EMA, RSI, ADX, MACD (вынести из candle_technicals) | ⭐⭐ | RSI считается вручную → потенциально разные результаты |
| 1.3 | **CVD/Delta**: перенести из whale_tracker | ⭐⭐ | CVD считается в трейдах — нужно синхронизировать |
| 1.4 | **Whales/Liquidity/Walls**: перенести из core/ | ⭐⭐ | Много зависимостей |
| 1.5 | **Funding/OI/Liquidations**: перенести из stores | ⭐⭐ | Данные с бирж |
| 1.6 | **Correlation/RS/Sector Breadth**: перенести из core/ | ⭐⭐⭐ | Engine-зависимости |
| 1.7 | **Volatility/Regime/Dominance**: перенести из adaptive.py | ⭐⭐ | Уже частично есть |
| ✅ | **Gate:** V1 и V2 сигналы показывают одинаковые результаты на FeatureStore | | |

### Фаза 2: Context Engine — L3 (1-2 недели)
**Цель:** Рынок описывается не числами, а контекстом (trend, volatility, sentiment).

| Шаг | Что делаем | Сложность | Риски |
|-----|-----------|-----------|-------|
| 2.1 | **Trend** — перенести trend_strength | ⭐ | Прямой перенос |
| 2.2 | **Volatility** — перенести adaptive.get_volatility_regime() | ⭐ | Уже есть |
| 2.3 | **Sentiment** — panic/euphoria/news mode (из ai_score._detect_regime) | ⭐⭐ | Субъективно |
| 2.4 | **Session** — London/NY/Asia — уже есть в core/session.py | ⭐ | Просто |
| 2.5 | **Liquidity** — spread + depth → L3 контекст | ⭐ | Перенос |
| ✅ | **Gate:** Context Engine выдаёт те же метки, что старый regime detector | | |

### Фаза 3: Strategy Engine — L4 + Signal Engine — L5 (3-4 недели)
**Цель:** 10-20 стратегий вместо 50 сигналов. Каждая — независимый модуль.

| Шаг | Что делаем | Сложность | Риски |
|-----|-----------|-----------|-------|
| 3.1 | **Strategy: Volume** — volume_spike + volume_body + taker_flow → 1 модуль | ⭐⭐ | Агрегация скоринга |
| 3.2 | **Strategy: Whale** — whale + buy_sell_ratio + cluster_buy + whale_accum | ⭐⭐ | 4→1 с сохранением качества |
| 3.3 | **Strategy: Momentum** — momentum + consecutive (трендовая часть) | ⭐⭐ | Просто |
| 3.4 | **Strategy: Reversal** — rsi + candle_pattern + cvd_divergence + divergence | ⭐⭐⭐ | Комбинация разных источников — сложно |
| 3.5 | **Strategy: Breakout** — inside_bar + volume_body → breakout detection | ⭐⭐ | Новый тип сигнала |
| 3.6 | **Strategy: Trend** — pullback + consecutive + trend_strength | ⭐⭐ | Средне |
| 3.7 | **Strategy: Orderbook** — orderbook + wall_stacked + bid_ask_ratchet | ⭐⭐ | 3→1 |
| 3.8 | **Strategy: Liquidation** — liquidation + liquidation_cascade + squeeze_setup + liquidation_cluster | ⭐⭐ | 4→1 |
| 3.9 | **Strategy: Rotation** — sector_rotation + sector_strength + market_leader + rs_momentum + rotation | ⭐⭐⭐ | 5→1 — самая сложная стратегия |
| 3.10 | **Strategy: Mean Reversion** — rsi (крайние значения) + spread_widening | ⭐⭐ | Новая |
| 3.11 | **Signal Engine** — каждая стратегия выдаёт Entry/Exit/TP/SL | ⭐⭐⭐ | Интерфейс сигнала меняется |
| ✅ | **Gate:** Каждая стратегия покрывает те же случаи, что старые сигналы | | |

### Фаза 4: Consensus Engine — L7 (2 недели)
**Цель:** smart_money + ai_score + wall_bounce + whale_wall → единое голосование.

| Шаг | Что делаем | Сложность | Риски |
|-----|-----------|-----------|-------|
| 4.1 | Consensus Engine: каждая стратегия голосует (buy/sell/neutral) | ⭐⭐⭐ | Веса нужно калибровать |
| 4.2 | Time Decay: уверенность падает без подтверждений | ⭐⭐ | Просто |
| 4.3 | Market Pulse: breadth + volume + RS + OI + funding → 0-100 | ⭐⭐ | Новый модуль |
| 4.4 | Opportunity Scanner: сортировка сделок по качеству | ⭐⭐ | На основе Consensus |
| ✅ | **Gate:** Consensus Engine >= old smart_money + ai_score по качеству | | |

### Фаза 5: Risk Engine — L6 + Explain Engine — L7 (1-2 недели)
| Шаг | Что делаем | Сложность | Риски |
|-----|-----------|-----------|-------|
| 5.1 | Risk Engine: размер позиции, спред, confidence, RR | ⭐⭐⭐ | Зависит от точности Feature Engine |
| 5.2 | Explain Engine: человеко-читаемые объяснения | ⭐ | Замена signal_explain |
| ✅ | **Gate:** Risk Engine не блокирует прибыльные сделки | | |

### Фаза 6: Learning Engine — L8 + Intelligence Engine — L9 (2-3 недели)
| Шаг | Что делаем | Сложность | Риски |
|-----|-----------|-----------|-------|
| 6.1 | Learning Engine: оценка сигнала через 30m/1h/4h/24h | ⭐⭐⭐ | Данные, storage, аналитика |
| 6.2 | Market Memory: хранение 100K сигналов | ⭐⭐ | Storage |
| 6.3 | Intelligence Engine: поиск похожих ситуаций | ⭐⭐⭐ | Перенос pattern_similarity + ai_cluster |
| 6.4 | Strategy Rating: рейтинг по стратегиям | ⭐⭐ | На основе Learning Engine |
| ✅ | **Gate:** Learning Engine работает в production без ошибок | | |

### Фаза 7: UI — L10 (1 неделя)
| Шаг | Что делаем | Сложность | Риски |
|-----|-----------|-----------|-------|
| 7.1 | Telegram: новый формат с explain tree | ⭐⭐ | Не сломать старый |
| 7.2 | Dashboard: heatmap + ranking | ⭐⭐ | Чисто UI |
| 7.3 | REST/WebSocket API | ⭐⭐⭐ | Документация |

### Фаза 8: Отключение V1 (1 неделя)
| Шаг | Что делаем | Сложность | Риски |
|-----|-----------|-----------|-------|
| 8.1 | Переключить V2_ENABLED = True, V1_ENABLED = False | ⭐ | **Критично** |
| 8.2 | A/B сравнение в production 48h | ⭐⭐ | Может пропустить баги |
| 8.3 | Удалить старые файлы сигналов | ⭐ | Безопасно, если V2 работает |
| ✅ | **Gate:** V2 стабильно работает 48h без ошибок | | |

---

## 4. Критические точки (что сломается)

### 🔴 CRITICAL: Bridge между старой шиной и FeatureStore
- **Проблема:** V1 сигналы получают SignalContext.build_context() из старой шины. Если FeatureStore не синхронизирован, V2 будет жить своей жизнью.
- **Решение:** FeatureStore пишется из той же шины. V1 сигналы читают из старого контекста, V2 — из FeatureStore. Параллельно, без пересечения.
- **Проверка:** Логировать оба значения и сравнивать.

### 🔴 CRITICAL: Адаптивные пороги (core/adaptive.py)
- **Проблема:** Пороги сейчас рассчитываются в VolatilityTracker внутри _build_context(). В V2 это должно быть в Feature Engine.
- **Решение:** VolatilityTracker остаётся как есть, но Feature Engine его вызывает. Обратная совместимость — тривиальна.
- **Проверка:** adaptive.get("volume_z") возвращает тот же результат.

### 🟡 HIGH: RSI и другие индикаторы в candle_technicals
- **Проблема:** RSI считается вручную в signals/candle_technicals.py. В Feature Engine будет стандартная TA-Lib/библиотечная реализация.
- **Решение:** Пока V1 работает — RSI считается по-старому. V2 использует библиотеку. Сравнить результаты на 10K свечей.
- **Проверка:** `assert abs(rsi_v1 - rsi_v2) < 0.01`

### 🟡 HIGH: Состояние сигналов (static vars)
- **Проблема:** RSRankingSignal использует `_last_top`, `_last_bottom` — статические переменные класса. В V2 стратегии stateless.
- **Решение:** Вынести состояние в FeatureStore или ContextStore.

### 🟡 HIGH: AIScoreSignal — монстр 543 строки
- **Проблема:** ai_score содержит копии логики volume, whale, cvd, ob, liquidation, momentum. При переписывании легко пропустить нюанс.
- **Решение:** Сначала разбить ai_score на отдельные модули в Feature Engine, потом собрать Consensus Engine из них.

### 🟡 MEDIUM: Engine signals (market_breadth, heatmap, rotation и др.)
- **Проблема:** Эти сигналы генерируются core-движками, а не через check(). Их check() = None. Нужно понять, кто и как их создаёт.
- **Решение:** Каждый core engine должен писать в FeatureStore, а V2 Strategy Engine читать оттуда.

### 🟢 LOW: Пассивные сигналы (signal_dna, pattern_similarity и др.)
- **Проблема:** Никакая — они check() = None. Просто удалить/переименовать.

---

## 5. Обеспечение backward compatibility

### Принцип: Dual-Run
```
Старая шина ─→ V1 SignalEngine ─→ V1 Dispatcher ─→ Telegram
      │
      └──→ FeatureStore ─→ V2 Feature Engine ─→ V2 Strategy Engine ─→ V2 Consensus ─→ Telegram (тест)
```

### Правила:
1. **V1_ENABLED = True** (по умолчанию) — всё работает как раньше
2. **V2_ENABLED = False** (по умолчанию) — V2 не влияет на прод
3. **V2_DRY_RUN = True** — V2 работает, но не отправляет сигналы (только лог)
4. **V2_SIGNALS = [список]** — конкретные сигналы, которые уже переехали

### Мосты:
- **AdapterBridge:** Старый `SignalContext` → новый `FeatureQuery` (пока V1 жив)
- **ReverseBridge:** Новый `FeatureStore` → старый `SignalContext` (для V1 на новых данных)
- **LoggingBridge:** Сравнение V1 vs V2 результатов в логах

### Процесс переключения сигнала:
```
1. Пишем Feature Engine калькуляторы (признаки)
2. Пишем V2 стратегию (использует FeatureStore)
3. Включаем V2_DRY_RUN для этой стратегии → сравниваем с V1
4. Расхождение < 1% → включаем V2_SIGNALS
5. Через неделю → выключаем V1 сигнал
```

---

## 6. Пример миграции: Whale Signal (whale + cluster_buy + buy_sell_ratio + whale_accum)

### Текущее состояние (V1)
4 отдельных сигнала в `signals/`:
- `whale.py` — WhaleSignal (крупные сделки)
- `trade_flow.py` — ClusterBuySignal (кластер), BuySellRatioSignal (соотношение), WhaleAccumSignal (дистрибуция)

Каждый считает свои метрики, каждый заново фильтрует whale_trades из контекста.

### Новая архитектура (V2)

#### Шаг 1: Feature Engine L2 — Whale Features
```python
# feature_engine/whale.py

class WhaleFeatureCalculator:
    """Единый калькулятор китовых признаков. Кэшируется в FeatureStore."""

    async def calculate(self, symbol: str) -> dict:
        trades = whale_tracker.get_whales(symbol, threshold=100_000)
        # Все признаки считаются ОДИН раз
        return {
            "whale_total_volume": ...,      # суммарный объём
            "whale_buy_volume": ...,         # buy объём
            "whale_sell_volume": ...,        # sell объём
            "whale_buy_sell_ratio": ...,     # buy_sell_ratio
            "whale_buy_count": ...,          # кол-во buy
            "whale_sell_count": ...,         # кол-во sell
            "whale_biggest_trade": ...,      # максимальная сделка
            "whale_cluster_buy": ...,        # 3+ buy за 5 мин
            "whale_cluster_sell": ...,       # 3+ sell за 5 мин
            "whale_accumulation": ...,       # дистрибуция/накопление
            "whale_net_flow_pct": ...,       # net flow в %
        }
```

#### Шаг 2: Strategy Engine L4 — Whale Strategy
```python
# strategy/whale.py

class WhaleStrategy(BaseStrategy):
    """Стратегия Whale — использует FeatureStore, выдаёт 1 сигнал."""

    def score(self, features: dict, context: ContextSnapshot) -> SignalCandidate:
        score = 0
        direction = "neutral"
        factors = []

        # Фактор 1: Крупные сделки (было: whale.py)
        if features["whale_biggest_trade"]["notional"] > 1_000_000:
            score += 25
            factors.append(WhaleFactor("megawhal", 25))

        # Фактор 2: Кластер (было: cluster_buy)
        if features["whale_cluster_buy"]["count"] >= 3:
            score += 20
            factors.append(WhaleFactor("cluster", 20))

        # Фактор 3: Соотношение (было: buy_sell_ratio)
        if features["whale_buy_sell_ratio"] > 2.0:
            score += 15

        # Фактор 4: Накопление/дистрибуция (было: whale_accum)
        if features["whale_accumulation"]["type"] == "accumulation":
            score += 20

        if score < 30:
            return None

        return SignalCandidate(
            strategy="whale",
            direction=...,     # определяется голосованием факторов
            score=min(100, score + 40),
            factors=factors,
        )
```

#### Шаг 3: Entry/Exit из одной стратегии
```python
# V2 Signal Engine
signal = strategy.evaluate(features, context)
signal.entries = [Entry(side="buy", price=best_bid, confidence=0.85)]
signal.exits = [Exit(stop_loss=..., take_profit=...)]
signal.tp_sl = {"tp1": ..., "tp2": ..., "sl": ...}
```

#### Шаг 4: Dual-Run сравнение
```
V1:
  whale(score=80, direction=buy, meta={value: $500K})
  cluster_buy(score=65, direction=buy, meta={count: 4})
  buy_sell_ratio(score=55, direction=buy, meta={ratio: 2.3})

V2 (WhaleStrategy):
  whale(score=82, direction=buy, factors=[cluster(20), megawhal(25), ratio(15)])
  
✅ V2.score ≈ V1.max(80,65,55) → 82 vs 80 — OK (расхождение < 5%)
```

#### Шаг 5: Когда отключаем V1
- После 7 дней dual-run без ошибок
- V1_ENABLED для whale-сигналов = False
- V2_SIGNALS включает whale

---

## 7. Сводная таблица: 50 сигналов → 11 стратегий

| Стратегия V2 | Сигналы V1 | Уровень | Кол-во |
|-------------|-----------|---------|--------|
| **Volume** | volume_spike, volume_body, taker_flow | L4 | 3 |
| **Whale** | whale, buy_sell_ratio, cluster_buy, whale_accum | L4 | 4 |
| **Momentum** | momentum, consecutive (частично) | L4 | 2 |
| **Reversal** | rsi, candle_pattern, cvd_divergence, divergence | L4 | 4 |
| **Breakout** | inside_bar, volume_body (экстрим) | L4 | 2 |
| **Trend** | pullback, consecutive, trend_strength | L4 | 3 |
| **Orderbook** | orderbook, wall_stacked, bid_ask_ratchet, spread_widening, depth_ratio, iceberg | L4 | 6 |
| **Liquidation** | liquidation, liquidation_cascade, squeeze_setup, liquidation_cluster | L4 | 4 |
| **Rotation** | sector_rotation, sector_strength, market_leader, rs_momentum, rotation, rs_ranking | L4 | 6 |
| **Mean Reversion** | spread_widening, rsi (крайности) | L4 | 2 |
| **Smart Money / Consensus** | smart_money, ai_score, wall_bounce, whale_wall | L7 | 4 |

**Итого:** ~50 сигналов → **11 стратегий** + Feature Engine (L2) + Context Engine (L3) + Risk Engine (L6) + Explain Engine (L7) + Learning Engine (L8) + Intelligence Engine (L9)

---

## 8. Оценка трудозатрат

| Фаза | Описание | Недель | Разработчик |
|------|----------|--------|-------------|
| 0 | Подготовка инфраструктуры | 2 | Senior |
| 1 | Feature Engine L2 | 3 | 2× Middle |
| 2 | Context Engine L3 | 1.5 | Middle |
| 3 | Strategy Engine L4 (11 стратегий) | 4 | 2× Senior |
| 4 | Consensus Engine L7 | 2 | Senior |
| 5 | Risk + Explain Engine L6/L7 | 1.5 | Middle |
| 6 | Learning + Intelligence L8/L9 | 2.5 | Senior |
| 7 | UI L10 | 1 | Junior |
| 8 | Отключение V1 | 1 | Middle |
| **Итого** | | **~18.5 недель** | **4 чел.** |

### Параллельная работа:
- **Фаза 0** → **Фазы 1+2** (параллельно) → **Фаза 3** → **Фазы 4+5** (параллельно) → **Фаза 6** → **Фаза 7+8** (параллельно)
- Реальный календарь: **~12 недель** при 2-3 разработчиках

---

## 9. Быстрые победы (Low-hanging fruit)

Эти шаги можно сделать немедленно, без риска:

1. ✅ **Удалить/объединить** wall_bounce, whale_wall — они покроются Consensus Engine
2. ✅ **Переименовать** heatmap, market_replay, signal_explain — они не сигналы, а UI/инфра
3. ✅ **Вынести RSI, EMA, ADX** из сигналов в core/indicators.py (подготовка к L2)
4. ✅ **Создать пустой FeatureStore** — scaffold с интерфейсом
5. ✅ **Добавить V2_ENABLED флаг** в конфиг
6. ✅ **Разбить ai_score.py** на отдельные модули-калькуляторы (подготовка к Consensus)

---

## 10. Рекомендации

1. **Не переписывать всё сразу.** Feature Engine (L2) — самый безопасный первый шаг, он только добавляет данные.
2. **Dual-run обязателен.** Без сравнения V1 vs V2 вы не узнаете, что сломали.
3. **Начать с Volume и Whale стратегий** — они самые простые и дают быстрый win.
4. **AIScoreSignal — последним.** Это самый сложный сигнал с копиями всей логики. Легче написать Consensus Engine заново.
5. **Не трогать core-движки** (market_breadth, heatmap, rotation) в фазе 0 — они работают и не мешают.
6. **Сначала архитектура, потом код.** FeatureStore + шина V2 должны быть спроектированы до начала миграции.
7. **Бэкап signals.db, market_replay.db** перед любыми изменениями схемы данных.
