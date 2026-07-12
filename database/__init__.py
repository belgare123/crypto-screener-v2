"""Database session management."""

from __future__ import annotations

import logging

from config import settings
from database.models import init_db as _init_db

logger = logging.getLogger(__name__)

engine = None
session_factory = None


async def init_database():
    global engine, session_factory
    engine, session_factory = await _init_db(settings.database_url)
    logger.info("Database initialized: %s", settings.database_url)
