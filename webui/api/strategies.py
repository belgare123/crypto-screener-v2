"""Strategies API — list registered V2 strategies."""

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
    description: str = ""
    category: str = "v2"
    enabled: bool = True
    default_score: float = 0.0
    cooldown: int = 0
    timeframes: list[str] | None = None


# ── Routes ──

@router.get("/strategies", response_model=list[StrategySchema])
async def list_strategies():
    """List all registered V2 strategies."""
    from strategies import get_strategy_engine, list_strategies as ls
    engine = get_strategy_engine()
    return [
        StrategySchema(
            name=name,
            description=getattr(s, "description", "") or "",
            category="v2",
            enabled=True,
            default_score=getattr(s, "default_score", 0.0) or 0.0,
            cooldown=getattr(getattr(s, "meta", None), "cooldown", 0) or 0,
            timeframes=None,
        )
        for name, s in engine._strategy_by_name.items()
    ]


@router.get("/strategies/{name}", response_model=StrategySchema)
async def get_strategy(name: str):
    """Get a single strategy by name."""
    from strategies import get_strategy_engine
    engine = get_strategy_engine()
    s = engine._strategy_by_name.get(name)
    if s is None:
        raise HTTPException(404, f"Strategy '{name}' not found")
    return StrategySchema(
        name=name,
        description=getattr(s, "description", "") or "",
        category="v2",
        enabled=True,
        default_score=getattr(s, "default_score", 0.0) or 0.0,
        cooldown=getattr(getattr(s, "meta", None), "cooldown", 0) or 0,
        timeframes=None,
    )


@router.patch("/strategies/{name}", response_model=StrategySchema)
async def update_strategy(name: str, update: StrategySchema):
    """Placeholder — V2 strategies not dynamically configurable yet."""
    logger.info("Strategy update requested via Web UI: %s (not implemented in v0.10.0)", name)
    return await get_strategy(name)


@router.get("/strategies/categories", response_model=dict[str, int])
async def list_categories():
    """List strategy categories with counts."""
    from strategies import get_strategy_engine
    engine = get_strategy_engine()
    return {"v2": len(engine._strategy_by_name)}
