"""Unit tests for the narra_guide live-fetch layer.

Pins the four guardrails: URL is DERIVED from the credential's backend_base_url
(never hardcoded test/prod), the doc is cached in-process (not re-fetched every
call), and a fetch failure falls back to the bundled snapshot rather than
leaving the agent blind.
"""
import pytest

from xyz_agent_context.module.narramessenger_module import _narra_guide as ncg


@pytest.fixture(autouse=True)
def _clear_cache():
    ncg._cache.clear()
    yield
    ncg._cache.clear()


async def test_url_derived_from_backend_base_url(monkeypatch):
    seen = {}

    async def fake_get(url):
        seen["url"] = url
        return "# live guide"

    monkeypatch.setattr(ncg, "_http_get", fake_get)
    out = await ncg.fetch_guide("https://api-test.netmind.chat")
    assert seen["url"] == "https://api-test.netmind.chat/api/agent-guide/narra-runtime.md"
    assert out == "# live guide"

    out2 = await ncg.fetch_guide("https://api.netmind.chat")
    assert seen["url"] == "https://api.netmind.chat/api/agent-guide/narra-runtime.md"


async def test_trailing_slash_normalized(monkeypatch):
    seen = {}

    async def fake_get(url):
        seen["url"] = url
        return "x"

    monkeypatch.setattr(ncg, "_http_get", fake_get)
    await ncg.fetch_guide("https://api.netmind.chat/")
    assert "//api/agent-guide" not in seen["url"]


async def test_cache_avoids_second_fetch(monkeypatch):
    calls = {"n": 0}

    async def fake_get(url):
        calls["n"] += 1
        return "# guide"

    monkeypatch.setattr(ncg, "_http_get", fake_get)
    monkeypatch.setattr(ncg, "_now", lambda: 1000.0)
    await ncg.fetch_guide("https://api.netmind.chat")
    await ncg.fetch_guide("https://api.netmind.chat")
    assert calls["n"] == 1  # second call served from cache


async def test_cache_expires_after_ttl(monkeypatch):
    calls = {"n": 0}

    async def fake_get(url):
        calls["n"] += 1
        return "# guide"

    monkeypatch.setattr(ncg, "_http_get", fake_get)
    t = {"v": 1000.0}
    monkeypatch.setattr(ncg, "_now", lambda: t["v"])
    await ncg.fetch_guide("https://api.netmind.chat")
    t["v"] = 1000.0 + ncg._CACHE_TTL_SECONDS + 1
    await ncg.fetch_guide("https://api.netmind.chat")
    assert calls["n"] == 2  # refetched after TTL


async def test_fetch_failure_falls_back_to_snapshot(monkeypatch):
    async def boom(url):
        raise RuntimeError("network down")

    monkeypatch.setattr(ncg, "_http_get", boom)
    out = await ncg.fetch_guide("https://api.netmind.chat")
    # Bundled snapshot is non-empty and mentions narra-cli.
    assert out
    assert "narra-cli" in out.lower()


async def test_stale_cache_preferred_over_snapshot_on_failure(monkeypatch):
    async def once_then_fail(url):
        once_then_fail.n = getattr(once_then_fail, "n", 0) + 1
        if once_then_fail.n == 1:
            return "# fresh live doc"
        raise RuntimeError("down")

    monkeypatch.setattr(ncg, "_http_get", once_then_fail)
    t = {"v": 1000.0}
    monkeypatch.setattr(ncg, "_now", lambda: t["v"])
    first = await ncg.fetch_guide("https://api.netmind.chat")
    assert first == "# fresh live doc"
    # TTL expires; next fetch fails → serve the stale live copy, not the snapshot.
    t["v"] = 1000.0 + ncg._CACHE_TTL_SECONDS + 1
    second = await ncg.fetch_guide("https://api.netmind.chat")
    assert second == "# fresh live doc"
