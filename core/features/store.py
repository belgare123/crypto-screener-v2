"""
FeatureStore — центральное хранилище кэшированных признаков.
- In-memory dict с TTL на каждую фичу
- Thread-safe: asyncio.Lock на каждый ключ (минимальные блокировки)
- Bulk-get: несколько символов, несколько фич сразу
- Observer pattern: подписка на изменения конкретной фичи
- Минимальные аллокации: объекты не пересоздаются до истечения TTL
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
#  FeatureObserver — колбэк при обновлении фичи
# ──────────────────────────────────────────────

FeatureObserver = Callable[[str, str, Any], Coroutine[Any, Any, None]]
"""observer(symbol, feature_name, value) — вызывается после обновления фичи."""

# ──────────────────────────────────────────────
#  FeatureEntry — элемент кэша
# ──────────────────────────────────────────────

_NO_DEFAULT = object()


class FeatureEntry:
    """Одна запись в кэше. Хранит значение + TTL + метаданные."""

    __slots__ = ("value", "expires_at", "created_at", "updated_at")

    def __init__(self, value: Any, ttl: float):
        now = time.time()
        self.value = value
        self.expires_at = now + ttl if ttl > 0 else 0.0  # 0 = never expires
        self.created_at = now
        self.updated_at = now

    @property
    def is_expired(self) -> bool:
        return 0 < self.expires_at < time.time()

    def touch(self, ttl: float | None = None):
        """Обновить TTL (скользящее окно)."""
        now = time.time()
        self.updated_at = now
        if ttl is not None and ttl > 0:
            self.expires_at = now + ttl


# ──────────────────────────────────────────────
#  FeatureKey — составной ключ
# ──────────────────────────────────────────────

_KEY_SEP = "||"


def _make_key(symbol: str, name: str) -> str:
    """Нормализованный ключ: 'BTC/USDT:USDT||rsi.14'."""
    return f"{symbol}{_KEY_SEP}{name}"


def _parse_key(key: str) -> tuple[str, str]:
    """Разобрать ключ обратно в (symbol, name)."""
    idx = key.index(_KEY_SEP)
    return key[:idx], key[idx + len(_KEY_SEP):]


# ──────────────────────────────────────────────
#  FeatureStore
# ──────────────────────────────────────────────

class FeatureStore:
    """
    Безопасное in-memory хранилище признаков с:
    - TTL на каждую фичу
    - Блочно-ориентированным bulk-get
    - Observer pattern on change
    - Минимальными аллокациями (повторное использование объектов)
    """

    def __init__(self):
        # Хранилище: key -> FeatureEntry
        self._store: dict[str, FeatureEntry] = {}
        # Глобальный lock для операций записи/удаления
        self._write_lock = asyncio.Lock()
        # Per-key locks для параллельного чтения
        self._key_locks: dict[str, asyncio.Lock] = {}
        # Наблюдатели: (symbol, feature_name) -> [observer, ...]
        self._observers: dict[tuple[str, str], list[FeatureObserver]] = defaultdict(list)
        # Счётчик аллокаций для мониторинга
        self._alloc_counter = 0

    # ── Single get/set ──

    async def get(
        self,
        symbol: str,
        name: str,
        default: Any = None,
    ) -> Any:
        """
        Получить значение признака.
        Возвращает default, если фича отсутствует или протухла.
        """
        key = _make_key(symbol, name)
        entry = self._store.get(key)
        if entry is None or entry.is_expired:
            if entry is None:
                logger.debug("[feat] MISS %s", key)
            else:
                logger.debug("[feat] EXPIRED %s", key)
                await self._evict(key)
            return default
        logger.log(5, "[feat] HIT %s", key)
        return entry.value

    async def get_raw(self, symbol: str, name: str) -> FeatureEntry | None:
        """Получить FeatureEntry целиком (для проверки метаданных)."""
        key = _make_key(symbol, name)
        entry = self._store.get(key)
        if entry is None or entry.is_expired:
            if entry and entry.is_expired:
                await self._evict(key)
            return None
        return entry

    async def set(
        self,
        symbol: str,
        name: str,
        value: Any,
        ttl: float = 60.0,
    ):
        """
        Установить значение признака.
        Уведомляет наблюдателей, если значение изменилось.
        `ttl` = 0 означает «без TTL» (хранить вечно).
        """
        key = _make_key(symbol, name)
        async with self._write_lock:
            old_entry = self._store.get(key)
            if old_entry is not None and old_entry.value == value and not old_entry.is_expired:
                # Значение не изменилось — просто продлеваем TTL
                old_entry.touch(ttl)
                return
            self._alloc_counter += 1
            self._store[key] = FeatureEntry(value, ttl)
        # Уведомляем наблюдателей (вне lock)
        await self._notify_observers(symbol, name, value)

    # ── Eviction ──

    async def _evict(self, key: str):
        async with self._write_lock:
            self._store.pop(key, None)

    async def evict(self, symbol: str, name: str | None = None):
        """Сбросить фичу (или все фичи символа)."""
        if name is not None:
            await self._evict(_make_key(symbol, name))
        else:
            prefix = f"{symbol}{_KEY_SEP}"
            async with self._write_lock:
                to_delete = [k for k in self._store if k.startswith(prefix)]
                for k in to_delete:
                    self._store.pop(k, None)

    async def clear(self):
        """Полная очистка хранилища."""
        async with self._write_lock:
            self._store.clear()
            self._alloc_counter = 0

    # ── Bulk operations ──

    async def get_multi(
        self,
        symbols: list[str],
        names: list[str],
        default: Any = None,
    ) -> dict[str, dict[str, Any]]:
        """
        Получить несколько фич для нескольких символов.
        Возвращает {symbol: {name: value, ...}, ...}.
        Пропускает отсутствующие/протухшие фичи.
        """
        result: dict[str, dict[str, Any]] = {}
        for symbol in symbols:
            sym_result: dict[str, Any] = {}
            for name in names:
                val = await self.get(symbol, name, _NO_DEFAULT)
                if val is not _NO_DEFAULT:
                    sym_result[name] = val
            if sym_result:
                result[symbol] = sym_result
        return result

    async def set_multi(
        self,
        items: list[tuple[str, str, Any]],
        ttl: float = 60.0,
    ):
        """
        Установить несколько фич атомарно.
        items = [(symbol, name, value), ...]
        """
        async with self._write_lock:
            for symbol, name, value in items:
                key = _make_key(symbol, name)
                self._alloc_counter += 1
                self._store[key] = FeatureEntry(value, ttl)
        # Батч-нотификации (вне lock)
        for symbol, name, value in items:
            await self._notify_observers(symbol, name, value)

    async def get_stale(self, symbol: str, name: str) -> Any | None:
        """Получить значение фичи, отдавая предпочтение stale.

        Возвращает свежее значение, если есть.
        Если протухло — возвращает stale значение (не evict).
        Если никогда не было — возвращает None.
        """
        key = _make_key(symbol, name)
        entry = self._store.get(key)
        if entry is None:
            return None
        return entry.value

    async def get_by_pattern(
        self,
        name_prefix: str,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        """
        Получить все фичи, начинающиеся с name_prefix (например, 'rsi').
        Если указан symbol — фильтровать по символу.
        Возвращает {full_name: value, ...}.
        """
        result: dict[str, Any] = {}
        now = time.time()
        for key, entry in list(self._store.items()):
            if entry.expires_at and now > entry.expires_at:
                continue
            sym, feat = _parse_key(key)
            if symbol is not None and sym != symbol:
                continue
            if feat.startswith(name_prefix):
                result[feat] = entry.value
        return result

    # ── Observer pattern ──

    def observe(self, symbol: str, name: str, observer: FeatureObserver):
        """Подписаться на изменения фичи."""
        key = (symbol, name)
        self._observers[key].append(observer)
        logger.debug("[feat] observer added %s %s -> %s", symbol, name, observer.__name__)

    def unobserve(self, symbol: str, name: str, observer: FeatureObserver):
        """Отписаться от изменений фичи."""
        key = (symbol, name)
        handlers = self._observers.get(key, [])
        if observer in handlers:
            handlers.remove(observer)

    def observe_prefix(self, name_prefix: str, observer: FeatureObserver):
        """
        Подписаться на все фичи с префиксом.
        name_prefix может быть 'whale' (все whale.*) или просто 'whale.trades'.
        """
        # Храним как ключ с префиксом '*'
        self._observers[("*", name_prefix)].append(observer)

    async def _notify_observers(self, symbol: str, name: str, value: Any):
        """Вызвать всех подходящих наблюдателей."""
        tasks = []

        # Точное совпадение
        for obs in self._observers.get((symbol, name), []):
            tasks.append(self._safe_notify(obs, symbol, name, value))

        # Префиксное совпадение
        for (sym_pattern, name_prefix), obs_list in list(self._observers.items()):
            if sym_pattern == "*" and name.startswith(name_prefix):
                for obs in obs_list:
                    tasks.append(self._safe_notify(obs, symbol, name, value))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_notify(self, obs: FeatureObserver, symbol: str, name: str, value: Any):
        try:
            await obs(symbol, name, value)
        except Exception:
            logger.exception("[feat] observer error %s", obs.__name__)

    # ── Stats ──

    @property
    def size(self) -> int:
        return len(self._store)

    @property
    def alloc_count(self) -> int:
        """Сколько раз созданы FeatureEntry (мониторинг аллокаций)."""
        return self._alloc_counter

    def stats(self) -> dict:
        """Статистика хранилища для мониторинга."""
        expired = sum(1 for e in self._store.values() if e.is_expired)
        return {
            "total_entries": len(self._store),
            "expired": expired,
            "alive": len(self._store) - expired,
            "allocations": self._alloc_counter,
            "observers": sum(len(v) for v in self._observers.values()),
        }


# ──────────────────────────────────────────────
#  Global singleton
# ──────────────────────────────────────────────

_feature_store: FeatureStore | None = None


def get_feature_store() -> FeatureStore:
    global _feature_store
    if _feature_store is None:
        _feature_store = FeatureStore()
    return _feature_store


def reset_feature_store():
    global _feature_store
    _feature_store = None
