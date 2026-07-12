"""Database models for signal history and user settings."""

from __future__ import annotations

import time

try:
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
    from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
    HAS_SQLALCHEMY = True
except ImportError:
    HAS_SQLALCHEMY = False

    # Stub for z/import
    class DeclarativeBase:  # type: ignore[no-redef]
        pass


class Base(DeclarativeBase):
    pass


class SignalLog(Base):
    """Лог отправленных сигналов."""
    __tablename__ = "signal_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_name: Mapped[str] = mapped_column(sa.String(64), index=True)
    symbol: Mapped[str] = mapped_column(sa.String(32), index=True)
    exchange: Mapped[str] = mapped_column(sa.String(16))
    score: Mapped[float]
    direction: Mapped[str] = mapped_column(sa.String(16))
    meta_json: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    ts: Mapped[float]  # unix ms
    created_at: Mapped[float] = mapped_column(default=time.time)


class SymbolConfig(Base):
    """Настройки по монетам: фильтры, whitelist/blacklist."""
    __tablename__ = "symbol_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(sa.String(32), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    min_score: Mapped[float | None] = mapped_column(nullable=True)
    blacklisted: Mapped[bool] = mapped_column(default=False)


class UserSettings(Base):
    """Пользовательские настройки."""
    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_chat_id: Mapped[str] = mapped_column(sa.String(64), unique=True)
    min_signal_score: Mapped[float] = mapped_column(default=40.0)
    enabled_signals: Mapped[str | None] = mapped_column(sa.Text, nullable=True)  # json list
    whitelist: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    blacklist: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    timeframes: Mapped[str | None] = mapped_column(sa.Text, nullable=True)


async def init_db(database_url: str):
    """Создать таблицы."""
    if not HAS_SQLALCHEMY:
        logger.warning("SQLAlchemy not installed — DB init skipped")
        return None, None

    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, session_factory
