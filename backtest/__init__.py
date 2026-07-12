"""Backtest framework — replay historical data through the same pipeline."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def run_backtest():
    """Запуск бэктеста через replay."""
    logger.info("Backtest not yet implemented — placeholder")
    logger.info("Pipeline: load historical data → MarketDataBus.replay() → signals")
    await asyncio.sleep(0.1)
