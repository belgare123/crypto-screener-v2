"""Конфигурация приложения."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="CS_",
    )

    # --- Биржи ---
    exchanges: list[str] = ["bybit", "binance"]
    testnet: bool = False

    # --- База данных ---
    database_url: str = "sqlite+aiosqlite:///data/screener.db"
    redis_url: str | None = None

    # --- Telegram ---
    telegram_token: str = ""
    telegram_chat_id: str = ""

    # --- API ---
    api_host: str = "127.0.0.1"
    api_port: int = 9118
    api_secret: str = "change-me"

    # --- Скрининг ---
    default_timeframe: str = "15m"
    max_pairs: int = 50          # сколько пар отслеживать
    min_volume_usdt: float = 1_000_000  # минимальный объём 24h
    min_signal_score: float = 60.0

    # --- Worker pool ---
    workers: int = 10
    queue_size: int = 1000

    # --- Anti-spam ---
    antispam_cooldown: int = 1800  # 30 min default
    antispam_degrade: bool = True  # не слать, если score ниже предыдущего

    # --- Safety ---
    max_ws_streams: int = 500

    # --- Data dirs ---
    data_dir: str = str(Path(__file__).parent.parent / "data")

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)


settings = Settings()
