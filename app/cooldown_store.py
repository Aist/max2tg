from __future__ import annotations

import time


class MemoryCooldownStore:
    def __init__(self):
        self._val: dict[str, str] = {}
        self._exp: dict[str, float] = {}

    def _cleanup_if_expired(self, key: str) -> None:
        now = time.time()
        exp = self._exp.get(key)
        if exp is not None and exp <= now:
            self._exp.pop(key, None)
            self._val.pop(key, None)

    async def ttl(self, key: str) -> int:
        self._cleanup_if_expired(key)
        exp = self._exp.get(key)
        if exp is None:
            return -2
        return int(exp - time.time())

    async def set(self, key: str, value: str, ex: int, nx: bool = False):
        self._cleanup_if_expired(key)
        if nx and key in self._val:
            return False
        self._val[key] = str(value)
        self._exp[key] = time.time() + max(1, int(ex))
        return True

    async def delete(self, key: str) -> None:
        self._exp.pop(key, None)
        self._val.pop(key, None)

    async def get(self, key: str) -> str | None:
        self._cleanup_if_expired(key)
        return self._val.get(key)

    async def incr(self, key: str) -> int:
        self._cleanup_if_expired(key)
        current = int(self._val.get(key, "0"))
        current += 1
        self._val[key] = str(current)
        if key not in self._exp:
            self._exp[key] = time.time() + 24 * 60 * 60
        return current

    async def expire(self, key: str, ex: int) -> bool:
        self._cleanup_if_expired(key)
        if key not in self._val:
            return False
        self._exp[key] = time.time() + max(1, int(ex))
        return True
