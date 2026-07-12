"""Cache — обёртка над Redis (или in-memory для dev)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

try:
    import orjson as json
except ImportError:
    import json  # type: ignore[no-redef]


class CacheBackend:
    """Абстракция кеша. По умолчанию — in-memory dict (без Redis на старте)."""

    def __init__(self, redis_url: str | None = None):
        self._redis_url = redis_url
        self._store: dict[str, tuple[float, bytes]] = {}  # key -> (expires_at, value)
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, data = entry
            if expires_at and time.time() > expires_at:
                del self._store[key]
                return None
        return json.loads(data) if data else None

    async def set(self, key: str, value: Any, ttl: int = 300):
        raw = json.dumps(value)
        expires = time.time() + ttl if ttl > 0 else 0
        async with self._lock:
            self._store[key] = (expires, raw)

    async def delete(self, key: str):
        async with self._lock:
            self._store.pop(key, None)

    async def exists(self, key: str) -> bool:
        async with self._lock:
            return key in self._store

    @property
    def size(self) -> int:
        return len(self._store)

    async def clear(self):
        async with self._lock:
            self._store.clear()


# singleton
_cache: CacheBackend | None = None

def get_cache() -> CacheBackend:
    global _cache
    if _cache is None:
        _cache = CacheBackend()
    return _cache
