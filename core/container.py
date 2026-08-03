from typing import TypeVar, Type, Dict, Any

T = TypeVar('T')

class Container:
    """A lightweight dependency injection container."""
    
    def __init__(self):
        self._services: Dict[Type[Any], Any] = {}
        
    def register(self, service_type: Type[T], instance: T) -> None:
        """Register a service instance."""
        self._services[service_type] = instance
        
    def get(self, service_type: Type[T]) -> T:
        """Get a registered service instance."""
        if service_type not in self._services:
            raise KeyError(f"Service {service_type.__name__} not registered in container.")
        return self._services[service_type]

# A global instance is optional, but it's usually better to attach it to the bot instance
# We will attach it to bot: `bot.container = Container()`
