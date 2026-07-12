"""Strategies API — list and manage registered signals."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["strategies"])

# ── Models ──

class StrategySchema(BaseModel):
    name: str
    description: str
    category: str
    enabled: bool
    default_score: float
    cooldown: int
    timeframes: list[str] | None = None


class StrategyUpdate(BaseModel):
    enabled: bool | None = None
    default_score: float | None = None
    cooldown: int | None = None


# ── Internal helpers ──

_imported = False


def _ensure_signals_loaded():
    """Импортируем signals чтобы заполнился реестр."""
    global _imported
    if _imported:
        return
    try:
        import signals  # noqa: F401
        _imported = True
    except ImportError:
        logger.warning("signals module not available — are we inside the project?")
    except Exception as e:
        logger.error("Failed to import signals: %s", e)


def _get_registry():
    _ensure_signals_loaded()
    try:
        from signals.base import list_signals, _signal_registry
        return list_signals(), _signal_registry
    except ImportError:
        return {}, {}


# ── Routes ──

@router.get("/strategies", response_model=list[StrategySchema])
async def list_strategies():
    """List all registered signals with metadata."""
    metas, _ = _get_registry()
    return [
        StrategySchema(
            name=meta.name,
            description=meta.description,
            category=meta.category,
            enabled=meta.enabled,
            default_score=meta.default_score,
            cooldown=meta.cooldown,
            timeframes=meta.timeframes,
        )
        for meta in metas.values()
    ]


@router.get("/strategies/{name}", response_model=StrategySchema)
async def get_strategy(name: str):
    """Get a single signal by name."""
    metas, _ = _get_registry()
    meta = metas.get(name)
    if not meta:
        raise HTTPException(404, f"Signal '{name}' not found")
    return StrategySchema(
        name=meta.name,
        description=meta.description,
        category=meta.category,
        enabled=meta.enabled,
        default_score=meta.default_score,
        cooldown=meta.cooldown,
        timeframes=meta.timeframes,
    )


@router.patch("/strategies/{name}", response_model=StrategySchema)
async def update_strategy(name: str, update: StrategyUpdate):
    """Enable/disable a signal or update its parameters."""
    metas, reg = _get_registry()
    meta = metas.get(name)
    if not meta:
        raise HTTPException(404, f"Signal '{name}' not found")

    if update.enabled is not None:
        meta.enabled = update.enabled
        logger.info("Signal '%s' enabled=%s (via Web UI)", name, update.enabled)
    if update.default_score is not None:
        meta.default_score = max(0.0, min(100.0, update.default_score))
    if update.cooldown is not None:
        meta.cooldown = max(0, update.cooldown)

    return StrategySchema(
        name=meta.name,
        description=meta.description,
        category=meta.category,
        enabled=meta.enabled,
        default_score=meta.default_score,
        cooldown=meta.cooldown,
        timeframes=meta.timeframes,
    )


@router.get("/strategies/categories", response_model=dict[str, int])
async def list_categories():
    """List signal categories with counts."""
    metas, _ = _get_registry()
    cats: dict[str, int] = {}
    for meta in metas.values():
        cats[meta.category] = cats.get(meta.category, 0) + 1
    return cats
