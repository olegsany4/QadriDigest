"""
llm/cache.py — Простой кэш ответов LLM (in-memory) с опциональным TTL.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional, Tuple

class SimpleLLMCache:
    def __init__(self, default_ttl: Optional[int] = None):
        self._store: Dict[Tuple[Any, ...], Tuple[float, Any]] = {}
        self._lock = threading.RLock()
        self._ttl = default_ttl  # seconds or None

    def get(self, key: Tuple[Any, ...]) -> Optional[Any]:
        now = time.time()
        with self._lock:
            rec = self._store.get(tuple(key))
            if not rec:
                return None
            exp, val = rec
            if exp and now > exp:
                # expired
                self._store.pop(tuple(key), None)
                return None
            return val

    def set(self, key: Tuple[Any, ...], value: Any, ttl: Optional[int] = None) -> None:
        with self._lock:
            effective_ttl = ttl if ttl is not None else self._ttl
            exp = (time.time() + effective_ttl) if effective_ttl else 0.0
            self._store[tuple(key)] = (exp, value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
