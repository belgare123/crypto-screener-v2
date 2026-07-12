# Crypto Screener v2 — Архитектура следующего поколения

> Операционная система для рынка, где сигналы — лишь один из результатов.

---

## Уровень 1. Источники данных (Data Sources)

```
Binance │ Bybit │ OKX │ Bitget │ Hyperliquid
```

**Market Data:**
- Trades
- Candles (1m, 5m, 15m, 1h)
- OrderBook (L1–L3)
- Liquidations
- Open Interest
- Funding Rate
- Ticker (24h stats)
- Mark Price
- Index Price

---

## Уровень 2. Feature Engine ⭐

Сердце системы. Все признаки вычисляются **один раз** и кэшируются.

### Price / Volume
- OHLCV, VWAP, ATR, Volume Profile

### Индикаторы
- EMA (8, 21, 50, 200), RSI, ADX, MACD

### Производные
- CVD, Delta, Aggression
- Whales, Liquidity, Spread
- Walls, Icebergs

### Фундаментальные
- Funding, Open Interest, Liquidations
- Correlation, Relative Strength
- Sector / Market Breadth

### Рыночные
- Volatility, Regime, Dominance
- Fear & Greed, News Sentiment
- On-chain метрики

**Принцип:** каждый feature вычисляется подписчиками на шину данных один раз, кэшируется в `FeatureStore`, и доступен всем стратегиям через асинхронный getter.

---

## Уровень 3. Context Engine

Не числа, а **понимание рынка**.

| Контекст | Значения |
|---|---|
| Trend | Bull / Bear / Flat |
| Volatility | Compression / Expansion |
| Sentiment | Panic / Euphoria / News Mode |
| Liquidity | Low / High |
| Session | London / NY / Asia / Weekend |

---

## Уровень 4. Strategy Engine

Стратегии, а не сигналы. Каждая стратегия — независимый модуль.

- Volume / Whale / Momentum
- Reversal / Breakout / Mean Reversion
- Scalping / Swing / Trend / Grid
- Smart Money / Liquidity Grab / Stop Hunt
- Market Maker / Funding / Carry
- Stat Arb

---

## Уровень 5. Signal Engine

Каждая стратегия может выдавать **несколько типов сигналов**:

- Entry / Exit / Take Profit / Stop
- Add Position / Reduce Position / Avoid Trade

---

## Уровень 6. Risk Engine

| Компонент | Описание |
|---|---|
| Position Size | Размер позиции |
| Volatility | Текущая волатильность |
| Spread / Slippage | Проскальзывание |
| Confidence | Уверенность в сигнале |
| Expected Move | Ожидаемое движение |
| Expected Drawdown | Ожидаемая просадка |
| RR / Probability | Risk/Reward + вероятность |

---

## Уровень 7. Explain Engine

Каждый сигнал **объясняется**.

```
BUY
├─ Whale       🟢
├─ Volume      🟢
├─ Delta       🟢
├─ Support     🟢
└─ Sector      🟢
```

---

## Уровень 8. Learning Engine ⭐

Автоматическая оценка после сигнала:

```
Signal → 30m → 1h → 4h → 24h
```

Система запоминает:
- Доходность
- Просадку
- Время до достижения цели
- Время до разворота

---

## Уровень 9. Intelligence Engine

Поиск **похожих ситуаций** в истории.

```
Volume│OI│Funding│Delta│Walls│Spread│Momentum
→ "Это похоже на BTC 14 марта 2024"
→ Вероятность: 83%
```

---

## Уровень 10. UI

```
Telegram → Dashboard → REST API → WebSocket API → Mobile
```

---

## Уникальные модули

### 1. Market Pulse ⭐⭐⭐⭐⭐
Каждые 5 секунд — здоровье рынка (0–100).
- Breadth + Volume + RS + OI + Funding + Volatility + Liquidity

### 2. Crowd Positioning ⭐⭐⭐⭐⭐
```
Retail: BUY  82%
Whales: SELL 61%
```

### 3. Opportunity Scanner ⭐⭐⭐⭐⭐
Сортировка сделок по качеству.
```
BTC 67 │ ETH 72 │ SOL 91 │ DOGE 48
```

### 4. Event Engine ⭐⭐⭐⭐⭐
Все необычные события:
- Funding Spike / OI Spike / Whale
- Liquidation / Wall / Gap
- News / Unlock / Listing / ETF

### 5. Market Memory ⭐⭐⭐⭐⭐
Хранит последние 100 000 сигналов и ищет похожие паттерны.

### 6. Strategy Rating ⭐⭐⭐⭐⭐
Рейтинг по стратегиям, а не по сигналам:
```
Breakout │ WinRate 74% │ PF 2.3 │ Expectancy 1.8%
```

### 7. Consensus Engine ⭐⭐⭐⭐⭐
Каждый модуль голосует → итоговый консенсус:
```
Volume: BUY │ Whale: BUY │ RS: BUY
Funding: SELL │ OI: BUY │ Momentum: BUY
→ Consensus: BUY 84%
```

### 8. Time Decay ⭐⭐⭐⭐
```
BUY 95 → 88 → 71 → 54
```
Без подтверждений уверенность падает автоматически.

---

## Что НЕ добавляем

- ❌ Десятки похожих свечных паттернов
- ❌ Множество RSI/MACD-вариаций
- ❌ Сигналы, отличающиеся лишь порогом
- ❌ Жёсткие веса в AI Score без исторической проверки

---

## Философия

**Вместо «100 сигналов» → «10–20 сильных независимых модулей», которые:**
- вычисляют признаки один раз
- могут использоваться в разных стратегиях
- оцениваются на исторических данных
- объясняют свои решения

Это делает систему проще в развитии и позволяет добавлять новые стратегии без переписывания ядра.
