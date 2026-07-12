"""Metrics API — fetch from Prometheus, serve REST + WS."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
from fastapi import APIRouter, Query
from pydantic import BaseModel

from webui.config import WebUIConfig

logger = logging.getLogger(__name__)

router = APIRouter(tags=["metrics"])

config = WebUIConfig()


async def fetch_prometheus() -> dict[str, Any]:
    """Fetch all relevant metrics from Prometheus."""
    queries = {
        "signals_total": "sum by(signal, symbol) (signals_total)",
        "signals_blocked": "sum by(reason) (signals_blocked)",
        "signal_errors": "sum by(signal) (signal_errors)",
        "dispatched_signals": "sum(dispatched_signals)",
        "dispatch_spam_blocked": "sum by(signal) (dispatch_spam_blocked)",
        "signals_rate": "rate(signals_total[5m])",
        "blocked_rate": "rate(signals_blocked[5m])",
        "dispatch_rate": "rate(dispatched_signals[5m])",
        "up": "up{job='screener'}",
    }
    result: dict[str, Any] = {}
    async with aiohttp.ClientSession() as session:
        for label, query in queries.items():
            try:
                url = f"{config.prometheus_url}/api/v1/query"
                params = {"query": query}
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        result[label] = body.get("data", {}).get("result", [])
                    else:
                        result[label] = []
            except Exception as e:
                logger.warning("Prometheus query '%s' failed: %s", label, e)
                result[label] = []
    return result


async def fetch_targets() -> list[dict]:
    """Fetch Prometheus target status."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{config.prometheus_url}/api/v1/targets"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    body = await resp.json()
                    return body.get("data", {}).get("activeTargets", [])
    except Exception as e:
        logger.warning("Failed to fetch targets: %s", e)
    return []


@router.get("/metrics")
async def get_metrics():
    """Get current metrics snapshot from Prometheus."""
    data = await fetch_prometheus()
    return {"status": "ok", "data": data}


@router.get("/targets")
async def get_targets():
    """Get Prometheus target status."""
    targets = await fetch_targets()
    return {"status": "ok", "targets": targets}


@router.get("/overview")
async def get_overview():
    """Aggregated overview — counts, rates, health."""
    data = await fetch_prometheus()
    targets = await fetch_targets()

    # Signal count
    total_signals = 0
    if data.get("signals_total"):
        for row in data["signals_total"]:
            total_signals += float(row.get("value", [0, 0])[1])

    total_blocked = 0
    if data.get("signals_blocked"):
        for row in data["signals_blocked"]:
            total_blocked += float(row.get("value", [0, 0])[1])

    total_dispatched = 0
    if data.get("dispatched_signals"):
        for row in data["dispatched_signals"]:
            total_dispatched += float(row.get("value", [0, 0])[1])

    total_errors = 0
    if data.get("signal_errors"):
        for row in data["signal_errors"]:
            total_errors += float(row.get("value", [0, 0])[1])

    prometheus_up = any(
        t.get("health") == "up" for t in targets
    ) if targets else None

    # Signal rate
    signal_rate = 0.0
    if data.get("signals_rate"):
        for row in data["signals_rate"]:
            signal_rate += float(row.get("value", [0, 0])[1])

    return {
        "total_signals": int(total_signals),
        "total_blocked": int(total_blocked),
        "total_dispatched": int(total_dispatched),
        "total_errors": int(total_errors),
        "signal_rate_5m": round(signal_rate, 2),
        "prometheus_up": prometheus_up,
        "screener_up": prometheus_up is True,
    }
