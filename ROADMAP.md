# Crypto-Screener v2 — Roadmap

> Основана на v1 (оригинал: `G:\bot\crypto-screener\`, v2: `G:\bot\crypto-screener-v2\`)
> Текущий v1 запущен, сигналы идут. v2 — площадка для разработки.

---

## Tier 1 — Must Have (качество сигналов)

### 1. Adaptive Thresholds
Сейчас пороги фиксированы (Volume Z > 3). Сделать `Threshold = f(volatility, session, symbol)`.
- Ночью BTC → z=2.2, во время CPI → z=5
- Меньше ложных сигналов в спокойные периоды

### 2. Session Engine
Определять сессии: Asia, London, NY, Overlap
- Особые веса/пороги для каждой сессии
- Пример: London Open → Momentum Weight 10→18

### 3. Correlation Engine
Иерархия: BTC → ETH → SOL → ALT
- Если SOL растёт раньше ETH — отдельный сигнал
- Поиск лидеров рынка

### 4. Relative Strength
Не `SOL +4%`, а `BTC +1% → SOL +4% | Relative Strength: +3%`
- Разница в процентах относительно BTC/ETH
- Информативнее абсолютных значений

### 5. Sector Scanner
Разделить рынок: AI, DeFi, Gaming, L2, RWA, Meme, DEX, Exchange
- Весь сектор AI растёт → сильный сигнал

---

## Tier 2 — Professional

### 6. Market Breadth
- Growing: 143 coins / Falling: 28
- 82% рынка зелёные

### 7. Heatmap Engine
Каждые 5 минут:
- TOP VOLUME, TOP MOMENTUM, TOP REVERSAL, TOP SHORTS

### 8. Rotation Detector
Деньги перетекают: L1 → Meme → AI
- Бот пишет: Rotation L1 → Meme

### 9. Liquidity Zones
Авто-построение HL: 64200, 64150, 64080

### 10. Trend Strength
Trend: 91% Healthy — не просто BUY/SELL

---

## Tier 3 — Уникальные

### 11. Signal DNA
Отпечаток сигнала: Volume 82, OI 91, Delta 77, Walls 63, Momentum 88
- Через месяц — поиск похожих ситуаций

### 12. Pattern Similarity
Новый сигнал → ищем 95% похож на BTC 14 March 2025
- Показываем, что тогда произошло

### 13. AI Clustering
ML без правил: Cluster 17 → WinRate 83%

### 14. Signal Lifecycle
Born → Growing → Confirmed → Weakening → Dead
- Не просто "сработал", а стадия жизни сигнала

### 15. Confidence Drift
Confidence 91 → 74 → 58 за 10 мин → ⚠ Signal weakening

### 16. Expected Move
Expected +2.3%, Probability 71%

### 17. Risk Meter
Risk: LOW / MEDIUM / HIGH на основе ATR + ликвидность + спред

### 18. Noise Filter
Рынок "пилит" → Noise 87% → Skip (не отправлять сигналы)

### 19. Market Replay
Открыть 2026-04-12 15:40: стакан, сделки, CVD, сигналы, AI Score

### 20. Explainable AI
Вместо Score: 91 — бот пишет почему:
- Объём > avg×4.1 (+22)
- CVD устойчиво растёт (+18)
- Стакан: перевес покупателей (+17)
- Кит на $1.3M (+15)
- Цена выше сопротивления (+14)

---

## Архитектурные направления (видение)

### Плагинная архитектура
Любой новый сигнал — отдельный модуль без изменения ядра (loader + schema).

### Rule Engine
Визуальный конструктор стратегий: "объём + OI + RSI" из готовых блоков.

### Бэктест + Рейтинг
Каждая стратегия → Win Rate, Profit Factor, avg return, max drawdown — автоматически.

### Интеллектуальная приоритизация
Вместо потока алертов — композитный сигнал с объяснением и прогнозом.

---

## Текущее состояние v1 (база для v2)
- ✅ 28 сигналов (Volume, Whale, Smart Money, Liquidation, Orderbook, Candle, Trade Flow, Hybrid)
- ✅ AI Score (6 факторов + MTF Confluence 1m/5m/15m)
- ✅ Антиспам (min_score=60, cooldown per-signal, degrade, dedup)
- ✅ SQLite статистика (SignalRecorder, WinRateChecker, StatsReporter)
- ✅ Bybit WS reconnect
- Telegram бот @ecrv3_bot → @moon_wils
