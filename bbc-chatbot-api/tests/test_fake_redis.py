"""The in-memory Redis the tests coordinate through (tests/fake_redis.py), where it must answer as Redis does: a
test that passes on a fake that answers otherwise proves nothing about the code it tests."""

import pytest

from tests.fake_redis import FakeRedis


async def _whole_scan(r: FakeRedis, key: str, cursor: int = 0, seen: dict | None = None) -> dict:
    seen = dict(seen or {})
    while True:
        cursor, part = await r.hscan(key, cursor=cursor, count=1)
        seen.update(part)
        if cursor == 0:
            return seen


@pytest.mark.asyncio
async def test_a_scan_begun_beside_another_never_moves_the_places_it_is_on():
    """CodeRabbit #288: scan A has had e0 (its cursor past it) when e0 is deleted and scan B begins, from 0, and goes
    through the hash: A, going on after, still gets e1, there all along (Redis: a whole scan returns every field
    there from its start to its end, whatever other scans run beside it). Rule 5: B, begun after e0 went, never gets
    it, and a field added meanwhile comes in both."""
    r = FakeRedis()
    await r.hset("h", mapping={"e0": "a", "e1": "b"})
    cursor_a, part_a = await r.hscan("h", cursor=0, count=1)
    assert part_a == {"e0": "a"} and cursor_a == 1
    await r.hdel("h", "e0")
    await r.hset("h", "e2", "c")
    assert await _whole_scan(r, "h") == {"e1": "b", "e2": "c"}
    assert await _whole_scan(r, "h", cursor_a, part_a) == {"e0": "a", "e1": "b", "e2": "c"}
