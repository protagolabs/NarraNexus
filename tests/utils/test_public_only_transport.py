"""
@file_name: test_public_only_transport.py
@author: Bin Liang
@date: 2026-10-29
@description: Connect-time SSRF pinning — the fix for the DNS-rebinding gap.

Before: callers validated a URL with assert_public_http_url (one DNS lookup) and
then an HTTP client resolved the hostname AGAIN when it connected. A short-TTL
attacker domain answers a public IP to the check and 169.254.169.254 (cloud
metadata) or an internal service to the client. PublicOnlyNetworkBackend moves
the check INTO the connect: it resolves, requires every address to be public,
and connects to a validated address — while TLS still verifies the hostname
(SNI + certificate) and the Host header is unchanged.
"""
from __future__ import annotations

import httpcore
import httpx
import pytest

from narranexus.platform.utils import safe_http
from narranexus.platform.utils.url_safety import UnsafeUrlError, assert_public_http_url

PUBLIC = "93.184.216.34"
METADATA = "169.254.169.254"


def _resolver(answers):
    """A resolver that returns the given answers in order (the last repeats)."""
    calls = []

    async def resolve(host, port):
        calls.append((host, port))
        return list(answers[min(len(calls) - 1, len(answers) - 1)])

    resolve.calls = calls
    return resolve


class _Stream(httpcore.AsyncNetworkStream):
    """Minimal fake connection: records TLS + written bytes, answers 200 'ok'."""

    def __init__(self, record):
        self._record = record
        self._sent_response = False

    async def write(self, buffer, timeout=None):
        self._record.setdefault("written", b"")
        self._record["written"] += buffer

    async def read(self, max_bytes, timeout=None):
        if self._sent_response:
            return b""
        self._sent_response = True
        return b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"

    async def aclose(self):
        pass

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self._record["server_hostname"] = server_hostname
        return self

    def get_extra_info(self, info):
        return None


class _InnerSpy(httpcore.AsyncNetworkBackend):
    """Stands in for the real socket backend: records where we were told to connect."""

    def __init__(self):
        self.record = {}
        self.connects = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connects.append((host, port))
        return _Stream(self.record)

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):  # pragma: no cover
        raise AssertionError("must never be reached")

    async def sleep(self, seconds):
        pass


def _client(resolver, inner):
    return httpx.AsyncClient(
        transport=safe_http.public_only_transport(resolver=resolver, inner=inner),
        follow_redirects=False,
    )


@pytest.mark.asyncio
async def test_rebinding_domain_is_refused_at_connect():
    # The incident follow-up's acceptance test: first answer public (passes the
    # pre-check), second answer the metadata IP (what the client would dial).
    resolve = _resolver([[PUBLIC], [METADATA]])
    url = "http://rebind.example/latest/meta-data/"
    assert await assert_public_http_url(url, resolver=resolve) == [PUBLIC]   # pre-check passes
    inner = _InnerSpy()
    async with _client(resolve, inner) as client:
        with pytest.raises(UnsafeUrlError):
            await client.get(url)
    assert inner.connects == []   # never dialled anything


@pytest.mark.asyncio
async def test_connects_to_the_validated_ip_keeping_host_and_sni():
    resolve = _resolver([[PUBLIC]])
    inner = _InnerSpy()
    async with _client(resolve, inner) as client:
        resp = await client.get("https://service.example/v1/models")
    assert resp.status_code == 200 and resp.text == "ok"
    assert inner.connects == [(PUBLIC, 443)]                       # dialled the validated IP
    assert inner.record["server_hostname"] == "service.example"   # TLS still checks the name
    assert b"Host: service.example" in inner.record["written"]     # Host header unchanged


@pytest.mark.asyncio
async def test_any_non_public_answer_refuses_the_host():
    inner = _InnerSpy()
    async with _client(_resolver([[PUBLIC, "10.0.0.5"]]), inner) as client:
        with pytest.raises(UnsafeUrlError):
            await client.get("http://mixed.example/")
    assert inner.connects == []


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    f"http://{METADATA}/latest/api/token",
    "http://127.0.0.1:8000/health",
    "http://[::1]/",
    "http://10.0.0.7:4000/",
])
async def test_literal_internal_address_is_refused_without_dns(url):
    resolve = _resolver([[PUBLIC]])
    inner = _InnerSpy()
    async with _client(resolve, inner) as client:
        with pytest.raises(UnsafeUrlError):
            await client.get(url)
    assert inner.connects == [] and resolve.calls == []


@pytest.mark.asyncio
async def test_dns_failure_is_a_refusal():
    async def boom(host, port):
        raise OSError("nxdomain")

    inner = _InnerSpy()
    async with _client(boom, inner) as client:
        with pytest.raises(UnsafeUrlError):
            await client.get("http://gone.example/")


@pytest.mark.asyncio
async def test_proxy_environment_is_ignored(monkeypatch):
    # A proxy would make the transport dial the PROXY, not the validated target.
    monkeypatch.setenv("HTTPS_PROXY", "http://10.0.0.99:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://10.0.0.99:3128")
    inner = _InnerSpy()
    async with _client(_resolver([[PUBLIC]]), inner) as client:
        await client.get("http://service.example/")
    assert inner.connects == [(PUBLIC, 80)]


@pytest.mark.asyncio
async def test_unix_socket_is_refused():
    backend = safe_http.PublicOnlyNetworkBackend(resolver=_resolver([[PUBLIC]]), inner=_InnerSpy())
    with pytest.raises(UnsafeUrlError):
        await backend.connect_unix_socket("/var/run/docker.sock")


# ── deployment-aware factory: cloud pins, local/desktop stays open ──

@pytest.mark.asyncio
async def test_egress_client_pins_in_cloud(monkeypatch):
    monkeypatch.setattr(safe_http.deployment_mode, "is_cloud_mode", lambda: True)
    async with safe_http.egress_client() as client:
        with pytest.raises(UnsafeUrlError):
            await client.get(f"http://{METADATA}/latest/api/token")


@pytest.mark.asyncio
async def test_egress_client_is_unrestricted_locally(monkeypatch):
    # Desktop/run.sh is one trusted user who legitimately points at localhost
    # (Ollama, LM Studio) — same contract as assert_public_http_url_in_cloud.
    monkeypatch.setattr(safe_http.deployment_mode, "is_cloud_mode", lambda: False)
    async with safe_http.egress_client() as client:
        assert not isinstance(client._transport, safe_http.PublicOnlyTransport)


# ── aiohttp: the same pin via the connector's resolver ──

import socket

import aiohttp


@pytest.mark.asyncio
async def test_aiohttp_resolver_refuses_rebinding_answer():
    resolver = safe_http.PublicOnlyResolver(resolver=_resolver([[METADATA]]))
    with pytest.raises(UnsafeUrlError):
        await resolver.resolve("rebind.example", 443, socket.AF_UNSPEC)


@pytest.mark.asyncio
async def test_aiohttp_resolver_returns_validated_addresses_with_hostname():
    resolver = safe_http.PublicOnlyResolver(resolver=_resolver([[PUBLIC, "2606:2800:220:1:248:1893:25c8:1946"]]))
    results = await resolver.resolve("service.example", 443, socket.AF_UNSPEC)
    assert [(r["host"], r["family"], r["hostname"]) for r in results] == [
        (PUBLIC, socket.AF_INET, "service.example"),
        ("2606:2800:220:1:248:1893:25c8:1946", socket.AF_INET6, "service.example"),
    ]
    only_v4 = await resolver.resolve("service.example", 443, socket.AF_INET)
    assert [r["host"] for r in only_v4] == [PUBLIC]


@pytest.mark.asyncio
async def test_aiohttp_session_with_pinned_connector_never_dials_internal(monkeypatch):
    # End to end through aiohttp: the session's GET must fail before any
    # socket is opened when the name resolves to the metadata IP.
    monkeypatch.setattr(safe_http.deployment_mode, "is_cloud_mode", lambda: True)
    from narranexus.platform.utils import url_safety

    monkeypatch.setattr(url_safety, "_default_resolver", _resolver([[METADATA]]))
    loop_dials = []

    async def no_connect(*args, **kwargs):  # pragma: no cover — must not run
        loop_dials.append(args)
        raise OSError("test: sockets disabled")

    async with aiohttp.ClientSession(connector=safe_http.egress_connector()) as session:
        monkeypatch.setattr(session.connector._loop, "create_connection", no_connect)
        with pytest.raises(UnsafeUrlError):  # not a DNS ClientError: the pin refused it
            await session.get("http://rebind.example/")
    assert loop_dials == []


@pytest.mark.asyncio
async def test_egress_connector_is_default_locally(monkeypatch):
    monkeypatch.setattr(safe_http.deployment_mode, "is_cloud_mode", lambda: False)
    connector = safe_http.egress_connector()
    try:
        assert not isinstance(connector._resolver, safe_http.PublicOnlyResolver)
    finally:
        await connector.close()
