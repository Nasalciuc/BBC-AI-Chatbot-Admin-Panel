"""An in-memory stand-in for redis.asyncio, enough for the code that coordinates through Redis.

Strings with NX/PX/EX and expiry, sorted sets, hashes, lists, counters, pipelines and the
Lua scripts the app sends (matched by text, never run). `down = True` makes every call
raise ConnectionError, as a Redis that does not answer would. A test whose code sends a
script the fake does not know fails loudly instead of passing on a guess.
"""

from __future__ import annotations

import fnmatch
import time
from typing import Any, Optional


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, Any] = {}
        self.expires: dict[str, float] = {}
        self.down = False
        self.calls: list[str] = []
        self.scripts: dict[str, Any] = {}  # script text -> python function(keys, args)

    # ── plumbing ─────────────────────────────────────────────
    def _check(self, name: str) -> None:
        self.calls.append(name)
        if self.down:
            raise ConnectionError("fake redis is down")

    def _alive(self, key: str) -> bool:
        exp = self.expires.get(key)
        if exp is not None and time.monotonic() >= exp:
            self.kv.pop(key, None)
            self.expires.pop(key, None)
        return key in self.kv

    def _set_expiry(self, key: str, px: Optional[int] = None, ex: Optional[int] = None) -> None:
        if px is not None:
            self.expires[key] = time.monotonic() + px / 1000.0
        elif ex is not None:
            self.expires[key] = time.monotonic() + ex
        else:
            self.expires.pop(key, None)

    def register(self, script: str, fn) -> None:
        """Teach the fake what a script does: fn(fake, keys, args) -> result."""
        self.scripts[script] = fn

    # ── strings ──────────────────────────────────────────────
    async def set(self, key: str, value: Any, nx: bool = False, xx: bool = False,
                  px: Optional[int] = None, ex: Optional[int] = None) -> Optional[bool]:
        self._check("set")
        exists = self._alive(key)
        if (nx and exists) or (xx and not exists):
            return None
        self.kv[key] = str(value)
        self._set_expiry(key, px=px, ex=ex)
        return True

    async def get(self, key: str) -> Optional[str]:
        self._check("get")
        return self.kv.get(key) if self._alive(key) else None

    async def delete(self, *keys: str) -> int:
        self._check("delete")
        n = 0
        for k in keys:
            if self._alive(k):
                self.kv.pop(k, None)
                self.expires.pop(k, None)
                n += 1
        return n

    async def exists(self, *keys: str) -> int:
        self._check("exists")
        return sum(1 for k in keys if self._alive(k))

    async def expire(self, key: str, seconds: int) -> bool:
        self._check("expire")
        if not self._alive(key):
            return False
        self._set_expiry(key, ex=seconds)
        return True

    async def pexpire(self, key: str, ms: int) -> bool:
        self._check("pexpire")
        if not self._alive(key):
            return False
        self._set_expiry(key, px=ms)
        return True

    async def ttl(self, key: str) -> int:
        self._check("ttl")
        if not self._alive(key):
            return -2
        exp = self.expires.get(key)
        return -1 if exp is None else max(0, int(exp - time.monotonic()))

    async def incr(self, key: str, amount: int = 1) -> int:
        self._check("incr")
        cur = int(self.kv.get(key, 0)) if self._alive(key) else 0
        cur += amount
        self.kv[key] = str(cur)
        return cur

    async def keys(self, pattern: str = "*") -> list[str]:
        self._check("keys")
        return [k for k in list(self.kv) if self._alive(k) and fnmatch.fnmatchcase(k, pattern)]

    # ── sorted sets ──────────────────────────────────────────
    def _zset(self, key: str) -> dict[str, float]:
        if not self._alive(key):
            self.kv[key] = {}
        return self.kv[key]

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self._check("zadd")
        z = self._zset(key)
        added = sum(1 for m in mapping if m not in z)
        for m, s in mapping.items():
            z[str(m)] = float(s)
        return added

    async def zrem(self, key: str, *members: str) -> int:
        self._check("zrem")
        z = self._zset(key)
        n = 0
        for m in members:
            if z.pop(str(m), None) is not None:
                n += 1
        return n

    @staticmethod
    def _bound(v: Any, default: float) -> float:
        if v in ("-inf",):
            return float("-inf")
        if v in ("+inf", "inf"):
            return float("inf")
        if v is None:
            return default
        return float(v)

    async def zrangebyscore(self, key: str, lo: Any, hi: Any, withscores: bool = False) -> list:
        self._check("zrangebyscore")
        z = self._zset(key)
        a, b = self._bound(lo, float("-inf")), self._bound(hi, float("inf"))
        items = sorted(((m, s) for m, s in z.items() if a <= s <= b), key=lambda t: (t[1], t[0]))
        return [(m, s) for m, s in items] if withscores else [m for m, _ in items]

    async def zrevrangebyscore(self, key: str, hi: Any, lo: Any, withscores: bool = False) -> list:
        out = await self.zrangebyscore(key, lo, hi, withscores=withscores)
        return list(reversed(out))

    async def zcount(self, key: str, lo: Any, hi: Any) -> int:
        return len(await self.zrangebyscore(key, lo, hi))

    async def zremrangebyscore(self, key: str, lo: Any, hi: Any) -> int:
        self._check("zremrangebyscore")
        z = self._zset(key)
        a, b = self._bound(lo, float("-inf")), self._bound(hi, float("inf"))
        gone = [m for m, s in z.items() if a <= s <= b]
        for m in gone:
            z.pop(m, None)
        return len(gone)

    async def zscore(self, key: str, member: str) -> Optional[float]:
        self._check("zscore")
        return self._zset(key).get(str(member))

    async def zcard(self, key: str) -> int:
        self._check("zcard")
        return len(self._zset(key))

    # ── hashes ───────────────────────────────────────────────
    def _hash(self, key: str) -> dict[str, str]:
        if not self._alive(key):
            self.kv[key] = {}
        return self.kv[key]

    async def hset(self, key: str, field: Optional[str] = None, value: Any = None,
                   mapping: Optional[dict] = None) -> int:
        self._check("hset")
        h = self._hash(key)
        items = dict(mapping or {})
        if field is not None:
            items[field] = value
        added = sum(1 for f in items if f not in h)
        for f, v in items.items():
            h[str(f)] = str(v)
        return added

    async def hgetall(self, key: str) -> dict[str, str]:
        self._check("hgetall")
        return dict(self.kv[key]) if self._alive(key) else {}

    async def hget(self, key: str, field: str) -> Optional[str]:
        self._check("hget")
        return self.kv[key].get(field) if self._alive(key) else None

    # ── lists ────────────────────────────────────────────────
    def _list(self, key: str) -> list:
        if not self._alive(key):
            self.kv[key] = []
        return self.kv[key]

    async def lpush(self, key: str, *values: Any) -> int:
        self._check("lpush")
        lst = self._list(key)
        for v in values:
            lst.insert(0, str(v))
        return len(lst)

    async def lrem(self, key: str, count: int, value: Any) -> int:
        self._check("lrem")
        lst = self._list(key)
        before = len(lst)
        lst[:] = [v for v in lst if v != str(value)]
        return before - len(lst)

    async def ltrim(self, key: str, start: int, end: int) -> bool:
        self._check("ltrim")
        lst = self._list(key)
        lst[:] = lst[start:(None if end == -1 else end + 1)]
        return True

    async def lrange(self, key: str, start: int, end: int) -> list:
        self._check("lrange")
        lst = self._list(key)
        return list(lst[start:(None if end == -1 else end + 1)])

    # ── scripts and pipelines ────────────────────────────────
    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        self._check("eval")
        fn = self.scripts.get(script)
        if fn is None:
            raise AssertionError("fake redis does not know this script; register() it in the test")
        keys = [str(k) for k in keys_and_args[:numkeys]]
        args = [str(a) for a in keys_and_args[numkeys:]]
        return fn(self, keys, args)

    def pipeline(self, transaction: bool = True) -> "FakePipeline":
        return FakePipeline(self)

    async def aclose(self) -> None:
        return None


class FakePipeline:
    def __init__(self, r: FakeRedis) -> None:
        self._r = r
        self._ops: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def queue(*args, **kwargs):
            self._ops.append((name, args, kwargs))
            return self
        return queue

    async def execute(self) -> list:
        out = []
        for name, args, kwargs in self._ops:
            out.append(await getattr(self._r, name)(*args, **kwargs))
        self._ops.clear()
        return out


def compare_and_delete(fake: FakeRedis, keys: list[str], args: list[str]) -> int:
    """What conversation_lock.RELEASE_LUA does."""
    key, token = keys[0], args[0]
    if fake._alive(key) and fake.kv.get(key) == token:
        fake.kv.pop(key, None)
        fake.expires.pop(key, None)
        return 1
    return 0
