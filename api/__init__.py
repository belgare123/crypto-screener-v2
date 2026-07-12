"""FastAPI web interface for configuration and signal feed."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from config import settings

logger = logging.getLogger(__name__)

app = FastAPI(title="crypto-screener API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/signals")
async def get_signals(limit: int = 20):
    """Последние N сигналов."""
    from signals.engine import get_engine
    engine = get_engine()
    results = []
    for _ in range(limit):
        sig = await engine.get_signal()
        if sig is None:
            break
        results.append({
            "signal": sig.signal_name,
            "symbol": sig.symbol,
            "score": sig.score,
            "direction": sig.direction,
            "ts": sig.ts,
            "meta": sig.meta,
        })
    return {"signals": results, "count": len(results)}


@app.get("/signals/list")
async def list_signals():
    """Список зарегистрированных типов сигналов."""
    from signals import list_signals
    return {"signals": list_signals()}


@app.get("/pairs")
async def get_pairs():
    """Активные пары."""
    from scanner.ticker import ticker_store
    return {"pairs": list(ticker_store.all.keys())}


@app.get("/whales/{symbol}")
async def get_whales(symbol: str):
    """Whale сделки для символа."""
    from scanner.trades import whale_tracker
    whales = whale_tracker.get_whales(symbol)
    return {"symbol": symbol, "whales": whales[-20:]}


async def serve_api():
    """Запуск FastAPI (из run.py)."""
    config = uvicorn.Config(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()
