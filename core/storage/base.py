from abc import ABC, abstractmethod
from typing import Optional, Dict, Any


class DataStore(ABC):
    """Базовый интерфейс для всех хранилищ данных."""

    @abstractmethod
    async def get(self, key: str, **kwargs) -> Optional[Dict[str, Any]]:
        """Получить данные по ключу."""
        pass

    @abstractmethod
    async def put(self, key: str, data: Dict[str, Any], **kwargs) -> None:
        """Сохранить данные."""
        pass

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Удалить данные по ключу."""
        pass
