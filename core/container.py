from typing import TypeVar, Type, Dict, Any

T = TypeVar('T')

class Container:
    def __init__(self):
        self._services: Dict[Any, Any] = {}

    def register(self, key: Any, instance: Any) -> None:
        self._services[key] = instance

    def get(self, key: Any) -> Any:
        if key not in self._services:
            key_name = key.__name__ if hasattr(key, '__name__') else str(key)
            raise KeyError(f"Service '{key_name}' not registered in container.")
        return self._services[key]
