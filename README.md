# crypto-screener-v2

**Modular cryptocurrency screening platform** — real-time market data pipeline with pluggable strategies, shadow-mode execution, and Docker deployment.

[![Python](https://img.shields.io/badge/python-3.11-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](LICENSE)

---

## Architecture

```
                          Telegram Bot (@ecrv3_bot)
                                │
                          Dispatcher (signal → alert)
                                │
                    ┌───────────┴───────────┐
                    │                       │
             Consensus Engine           OME (shadow)
                    │                       │
              State Engine           Risk Engine
                    │                       │
              Feature Engine           Learning Engine
                    │
         ┌──────────┴──────────┐
         │                     │
    Scanner Pool        Exchange WS
(candles, ticker,        (Bybit)
 trades, liq, ob)
         │                     │
         └──────────┬──────────┘
                    │
             Data Engine
         (normalization + cache)
```

All 9 engines run in **shadow mode** — strategies evaluate, risk filters, OME simulates orders — no real capital at risk.

## Features

- **Real-time WebSocket** — Bybit USDT perpetuals (kline, ticker, trades, liquidation, orderbook)
- **5 scanners** — Ticker, Candle, Trade, Liquidation, OrderBook
- **6 Feature Calculators** — Whale, Market, OHLCV, Volatility, OrderBook, Indicators
- **State Engine** — regime detection (ranging/trending/volatile) by symbol
- **Risk Engine** — pre-trade filters: spread, ATR, liquidity, session
- **Consensus Engine** — weighted voting across strategies + Opportunity Ranking
- **OME** — Order Management Engine (paper trading, shadow mode)
- **Learning Engine** — winrate tracking, dynamic weight adjustment
- **Docker** — containerized, health-checked, restart policy
- **Telegram alerts** — configurable proxy (SOCKS5 via `CS_TG_PROXY`)

## Quick Start

### Prerequisites

- Python 3.11+
- uv (recommended) or pip
- Docker (optional)

### Local

```bash
# Clone
git clone https://github.com/YOUR_USER/crypto-screener-v2.git
cd crypto-screener-v2

# Install
uv pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env — fill in CS_TELEGRAM_TOKEN, CS_TELEGRAM_CHAT_ID, CS_BYBIT_API_KEY, etc.

# Run
python run.py
```

### Docker

```bash
docker compose up -d
curl http://localhost:9120/health
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CS_BYBIT_API_KEY` | Yes | — | Bybit API key |
| `CS_BYBIT_API_SECRET` | Yes | — | Bybit API secret |
| `CS_TELEGRAM_TOKEN` | Yes | — | Telegram bot token |
| `CS_TELEGRAM_CHAT_ID` | Yes | — | Telegram chat/user ID |
| `CS_TG_PROXY` | No | — | SOCKS5 proxy for Telegram (e.g. `socks5://host.docker.internal:10808`) |
| `CS_DATABASE_URL` | No | `sqlite+aiosqlite:///./screener.db` | Database connection string |
| `CS_DB_PATH` | No | `./signals.db` | Path to signals DB file |
| `CS_LOG_LEVEL` | No | `INFO` | Logging level |

## Adding a Strategy

Strategies live in `strategies/` and inherit from `BaseStrategy`:

```python
# strategies/my_strategy.py
from strategies.base import BaseStrategy, Vote

class MyStrategy(BaseStrategy):
    async def evaluate(self, ctx: "Context") -> Vote:
        # Access features via ctx.features
        # Access state via ctx.state
        # Return Vote(direction="buy"|"sell"|"neutral", score=0-100, confidence=0-1)
        ...
```

Register in config and it loads automatically on next run.

## Project Structure

```
├── core/              Event bus, feature engine, state, risk, consensus
│   ├── features/      Feature calculators + engine
│   ├── risk/          Risk rules (spread, ATR, liquidity, session)
│   ├── consensus/     Weighted voting
│   ├── state/         Market regime detection
│   ├── ome/           Order Management Engine
│   ├── learning/      Winrate tracking, dynamic weights
│   └── monitoring/    Metrics server, healthcheck
├── exchanges/         Exchange adapters (Bybit WS)
├── scanner/           Data scanners (candles, ticker, trades, liq, ob)
├── strategies/        Pluggable trading strategies
├── alerts/            Telegram notifier
├── config/            Pydantic settings
├── run.py             Entry point
├── Dockerfile         Container build
└── docker-compose.yml Container orchestration
```

## API

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check (all engines) |
| `GET /metrics` | Prometheus metrics |
| `GET /` | Service info |

## Hyperopt (Parameter Optimization)

`run_hyperopt.py` — Optuna-based automatic parameter tuning for strategies.

```bash
# Optimize momentum_v2 parameters (50 trials, profit factor)
python run_hyperopt.py --symbol BTC/USDT:USDT --interval 1m \
    --start 2026-06-01 --end 2026-06-30 --metric profit_factor --n-trials 50

# Quick grid search (no Optuna)
python run_hyperopt.py \
    --grid momentum_threshold=0.3,0.5,1.0 min_consecutive=2,4,6

# Visualize results (requires optuna-dashboard)
optuna-dashboard sqlite:///optuna_studies.db
# → http://localhost:8080
```

**Outputs:**
| File | Description |
|---|---|
| `best_params.json` | Best parameter set found |
| `optuna_trials.csv` | Full trial history |
| `optuna_studies.db` | SQLite database (resumable) |

## License

MIT
