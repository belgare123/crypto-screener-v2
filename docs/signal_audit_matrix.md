# Phase 0 — Signal Audit Matrix

**Date:** 2026-07-11  
**Total:** 54 signals across 30 files  
**Engine:** V1 signal pipeline (`signals/engine.py` → `SignalEngine`)  

---

## 1. Complete Signal Registry

| # | Signal | File | Category | Data Sources | Uses FeatureEngine | Notes |
|---|--------|------|----------|-------------|-------------------|-------|
| 1 | `ai_cluster` | ai_clustering | ml | candles | ❌ | ML кластеризация |
| 2 | `ai_score` | ai_score | ai | candles, cvd, liq, ob, whale | ❌ | AI score ensemble |
| 3 | `market_breadth` | breadth | market_breadth | candles only | ❌ | Ширина рынка |
| 4 | `candle_pattern` | candle_patterns | candle | candles | ❌ | Паттерны свечей |
| 5 | `inside_bar` | candle_patterns | candle | candles | ❌ | Inside bar |
| 6 | `pullback` | candle_patterns | candle | candles | ❌ | Pullback detection |
| 7 | `rsi` | candle_technicals | candle | candles | ❌ | RSI manual |
| 8 | `momentum` | candle_technicals | candle | candles | ❌ | ROC momentum |
| 9 | `consecutive` | candle_technicals | candle | candles | ❌ | Consecutive candles |
| 10 | `volume_body` | candle_technicals | candle | candles | ❌ | Vol + body ratio |
| 11 | `consensus_v2` | consensus_v2 | candle | features | ✅ | FE-based consensus |
| 12 | `market_leader` | correlation | correlation | candles | ❌ | Leader-follower |
| 13 | `divergence` | correlation | correlation | candles | ❌ | RSI divergence |
| 14 | `market_alignment` | correlation | correlation | candles | ❌ | Alignment score |
| 15 | `signal_dna` | dna | ml | candles | ❌ | Signal fingerprint |
| 16 | `signal_explain` | explain | analytics | candles | ❌ | Explainability |
| 17 | `heatmap` | heatmap | heatmap | candles | ❌ | Top movers heatmap |
| 18 | `wall_bounce` | hybrid | hybrid | ob, liq, whale, cvd | ❌ | Wall bounce |
| 19 | `whale_wall` | hybrid | hybrid | ob, liq, whale, cvd | ❌ | Whale wall |
| 20 | `cvd_divergence` | hybrid | hybrid | ob, liq, whale, cvd | ❌ | CVD/price divergence |
| 21 | `bid_ask_ratchet` | hybrid | hybrid | ob, liq, whale, cvd | ❌ | Ratchet effect |
| 22 | `indicator_v2` | indicator_v2 | candle | features | ✅ | FE-based indicators |
| 23 | `signal_lifecycle` | lifecycle | ml | candles | ❌ | Lifecycle stage |
| 24 | `confidence_drift` | lifecycle | ml | candles | ❌ | Confidence change |
| 25 | `liquidation_cascade` | liquidation_advanced | liquidation | liq | ❌ | Cascade detection |
| 26 | `squeeze_setup` | liquidation_advanced | liquidation | liq | ❌ | Squeeze setup |
| 27 | `liquidation_cluster` | liquidation_advanced | liquidation | liq | ❌ | Cluster detection |
| 28 | `liquidation` | liquidation_signal | liquidation | liq, volume | ❌ | Simple liq signal |
| 29 | `liquidity_zone` | liquidity_zones | liquidity | candles | ❌ | HL/zone proximity |
| 30 | `expected_move` | market_analysis | analytics | candles | ❌ | ATR-based move est |
| 31 | `risk_meter` | market_analysis | analytics | candles | ❌ | Risk level meter |
| 32 | **`noise_filter`** | market_analysis | analytics | candles | ❌ | Noise detector |
| 33 | `orderbook` | orderbook_signal | orderbook | ob, volume | ❌ | OB imbalance |
| 34 | `spread_widening` | orderbook_signals | orderbook | ob | ❌ | Spread change |
| 35 | `depth_ratio` | orderbook_signals | orderbook | ob | ❌ | Bid/ask depth |
| 36 | `wall_stacked` | orderbook_signals | orderbook | ob | ❌ | Wall detection |
| 37 | `iceberg` | orderbook_signals | orderbook | ob | ❌ | Iceberg detection |
| 38 | `pattern_similarity` | pattern_similarity | ml | candles | ❌ | Historical pattern |
| 39 | `rs_momentum` | relative_strength | relative_strength | candles | ❌ | RS momentum |
| 40 | `rs_ranking` | relative_strength | relative_strength | candles | ❌ | RS ranking |
| 41 | `market_replay` | replay | analytics | candles | ❌ | Replay snapshot |
| 42 | `rotation` | rotation | rotation | candles | ❌ | Sector rotation |
| 43 | `rsi_v2` | rsi_v2 | candle | features | ✅ | FE-based RSI |
| 44 | `sector_rotation` | sector | sector | candles | ❌ | Sector rotation |
| 45 | `sector_strength` | sector | sector | candles | ❌ | Sector strength |
| 46 | `smart_money` | smart_money | smart_money | cvd, liq, ob, ticker, whale | ❌ | SMC patterns |
| 47 | `buy_sell_ratio` | trade_flow | trade_flow | whale_trades | ❌ | Buy/sell ratio |
| 48 | `taker_flow` | trade_flow | trade_flow | whale_trades | ❌ | Taker flow |
| 49 | `cluster_buy` | trade_flow | trade_flow | whale_trades | ❌ | Cluster buy |
| 50 | `whale_accum` | trade_flow | trade_flow | whale_trades | ❌ | Whale accumulation |
| 51 | `trend_strength` | trend_strength | trend | candles | ❌ | Trend direction |
| 52 | `volume_spike` | volume | volume | candles | ❌ | Volume spike |
| 53 | `whale` | whale | whale | whale_trades | ❌ | Whale trade |
| 54 | `whale_v2` | whale_v2 | whale | whale_trades, cvd | ✅ | FE-based whale |

---

## 2. Duplicates & Quick Wins

### 2.1 RSI Variants (3 signals)
| Signal | File | Approach | Priority |
|--------|------|----------|----------|
| `rsi` | candle_technicals | Manual RSI(14) from OHLCV | ⚪ V1 legacy |
| `indicator_v2` | indicator_v2 | FeatureEngine RSI + EMA + MACD + BB | 🟢 Keep for v3 |
| `rsi_v2` | rsi_v2 | FeatureEngine RSI only | 🟡 Merge into indicator_v2 |

**Action:** `rsi` → shadow mode, `rsi_v2` → merge into `indicator_v2`

### 2.2 Whale Variants (3 signals)
| Signal | File | Approach | Priority |
|--------|------|----------|----------|
| `whale` | whale | Manual whale_trades scan | ⚪ V1 legacy |
| `whale_v2` | whale_v2 | FeatureEngine + CVD | 🟢 Keep for v3 |
| `whale_wall` | hybrid | OB walls + whale trades | 🟢 Keep as hybrid |

**Action:** `whale` → shadow mode

### 2.3 Liquidation Variants (4 signals)
| Signal | File | Approach | Priority |
|--------|------|----------|----------|
| `liquidation` | liquidation_signal | Simple threshold | ⚪ V1 legacy |
| `liquidation_cascade` | liquidation_advanced | Cascade pattern | 🟢 Keep |
| `squeeze_setup` | liquidation_advanced | Squeeze detection | 🟢 Keep |
| `liquidation_cluster` | liquidation_advanced | Cluster detection | 🟢 Keep |

**Action:** `liquidation` → shadow mode, others keep

### 2.4 OrderBook Variants (5 signals)
| Signal | File | Approach | Priority |
|--------|------|----------|----------|
| `orderbook` | orderbook_signal | Simple imbalance | 🟡 Merge into orderbook_signals |
| `spread_widening` | orderbook_signals | Spread delta | 🟢 Keep |
| `depth_ratio` | orderbook_signals | Bid/ask depth | 🟢 Keep |
| `wall_stacked` | orderbook_signals | Wall detect | 🟢 Keep |
| `iceberg` | orderbook_signals | Iceberg detect | 🟢 Keep |

**Action:** `orderbook` → shadow mode, consolidate into `orderbook_signals`

### 2.5 V1→V2 Shadow Plan
| # | Signal | V1 File | V2 Status | Gate |
|---|--------|---------|-----------|------|
| 1 | `rsi` → `indicator_v2` | candle_technicals | 🟢 compare mode | 5% score diff |
| 2 | `whale` → `whale_v2` | whale | 🟢 compare mode | 5% score diff |
| 3 | `liquidation` → `liquidation_cascade` | liquidation_signal | 🟢 compare mode | 5% score diff |
| 4 | `orderbook` → `orderbook_signals` | orderbook_signal | 🟢 compare mode | 5% score diff |

---

## 3. FeatureEngine Coverage

### 3.1 Signals already on FeatureEngine
- `consensus_v2` — FE-based consensus ✅
- `indicator_v2` — FE indicators (RSI, EMA, MACD, BB) ✅
- `rsi_v2` — FE-based RSI ✅
- `whale_v2` — FE-based whale ✅

### 3.2 Signals that need FeatureEngine migration (Phase 1+)
| Category | Signals | Migration Priority |
|----------|---------|-------------------|
| **Trend** | `trend_strength` | 🟢 Phase 1 |
| **Volatility** | `expected_move`, `risk_meter` | 🟢 Phase 1 |
| **Volume** | `volume_spike`, `volume_body` | 🟢 Phase 2 |
| **OrderBook** | all 5 orderbook signals | 🟢 Phase 2 |
| **Liquidation** | all 4 liquidation signals | 🟢 Phase 3 |
| **Whale/Trade** | all 4 trade_flow + whale_v2 | 🟢 Phase 2 |
| **Hybrid** | all 4 hybrid signals | 🟡 Phase 4 |
| **Correlation** | all 3 correlation signals | 🟡 Phase 5 |
| **Sector** | `sector_rotation`, `sector_strength` | 🟡 Phase 5 |

### 3.3 Analytics/UI signals (no migration needed)
- `heatmap`, `rotation`, `market_breadth`, `signal_dna`, `signal_explain`,
  `signal_lifecycle`, `confidence_drift`, `pattern_similarity`,
  `market_replay`, `ai_cluster`, `ai_score`, `noise_filter`

---

## 4. Events Already Defined (from V1 signal patterns)

| Event Type | Triggered by | Signals Consuming |
|------------|-------------|-------------------|
| `candle.close` | 1m candle close | All candle + trend + volume signals |
| `trades.whale` | Trade > threshold | whale, whale_v2, trade_flow*, smart_money, hybrid* |
| `ob.wall` | Wall detected | orderbook_signals*, hybrid* |
| `ob.iceberg` | Iceberg detected | iceberg |
| `liquidation.cluster` | Liq cluster | liquidation_advanced* |
| `liquidation.cascade` | Cascade trigger | liquidation_cascade |
| `cvd.divergence` | CVD vs price | cvd_divergence |
| `volume.spike` | Volume spike | volume_spike |

---

## 5. Audit Summary

| Metric | Value |
|--------|-------|
| Total signals | 54 |
| Unique categories | 17 |
| Already on FeatureEngine | 4 (7.4%) |
| Manual-indicator signals | 45 (83.3%) |
| Analytics/UI signals | 5 (9.3%) |
| Shadow candidates (quick wins) | 4 |
| Noise filter already exists | ✅ `noise_filter` in market_analysis |
