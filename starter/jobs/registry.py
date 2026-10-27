"""Job handler registry. Each module registers its handlers on import."""
_registry: dict = {}


def register(kind: str, handler):
    """Register a callable handler for a job kind."""
    _registry[kind] = handler


def get_handler(kind: str):
    return _registry.get(kind)


def all_kinds() -> list:
    return list(_registry.keys())
