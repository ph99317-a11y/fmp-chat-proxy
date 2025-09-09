import time
from typing import Any, Dict, Tuple, Optional

class TTLCache:
    def __init__(self, ttl_seconds: int = 60, max_items: int = 1000):
        self.ttl = ttl_seconds
        self.max_items = max_items
        self._store: Dict[str, Tuple[float, Any]] = {}

    def _evict_if_needed(self):
        if len(self._store) <= self.max_items:
            return
        oldest_key = min(self._store, key=lambda k: self._store[k][0])
        self._store.pop(oldest_key, None)

    def get(self, key: str) -> Optional[Any]:
        now = time.time()
        item = self._store.get(key)
        if not item:
            return None
        ts, value = item
        if now - ts > self.ttl:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any):
        self._store[key] = (time.time(), value)
        self._evict_if_needed()
