"""
@file_name: safe_http.py
@author: Bin Liang
@date: 2026-07-22
@description: Server-side HTTP GET that follows redirects with an SSRF gate on
every hop.

The ONE place the redirect-walk lives, so the security-critical per-hop SSRF
check has a single implementation (embed probe, page-text extraction, and the
future RenderService / streaming browser all share it — a second copy of this
loop is a place to forget the gate). Kept separate from `url_safety.py` so
that module stays stdlib-only (its stated contract); this one owns the httpx
dependency.

`safe_stream_get` is an async context manager yielding the FINAL response with
its body still streamable — the caller decides whether to read headers only
(probe) or a bounded slice of the body (text extraction).

Connect-time pinning (2026-10-29 incident follow-up, DNS rebinding): a URL
pre-check resolves the name once, then the HTTP client resolves it AGAIN when it
dials — a short-TTL attacker domain answers public to the first and the cloud
metadata IP or an internal service to the second. `PublicOnlyNetworkBackend`
puts the check inside the dial itself: resolve, require every address public,
connect to a validated address. TLS still uses the hostname (SNI + certificate
verification) and the Host header is untouched, because httpcore takes those
from the request origin, not from the address the socket was opened to.
`egress_client()` is the deployment-aware factory every server-side request to
a user-supplied URL uses: pinned in cloud, a plain client locally (desktop /
run.sh legitimately reach localhost — same contract as
`assert_public_http_url_in_cloud`). aiohttp callers get the same pin from
`egress_connector()`: a TCPConnector whose resolver only returns validated
public addresses, so the connector dials exactly what was checked.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Iterable, Optional, cast
from urllib.parse import urljoin

import aiohttp
import anyio
import httpcore
import httpx
from aiohttp.abc import AbstractResolver, ResolveResult

from narranexus.platform.utils import deployment_mode
from narranexus.platform.utils.url_safety import (
    Resolver,
    UnsafeUrlError,
    assert_public_http_url,
    resolve_public_addresses,
)

# A realistic desktop browser UA — some sites vary behaviour by UA, and we want
# what a real browser fetch would see. Single source for every safe fetch.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
MAX_REDIRECTS = 5


class RedirectLimitError(Exception):
    """More than MAX_REDIRECTS hops — treated as a fetch failure by callers."""


@asynccontextmanager
async def safe_stream_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    resolver: Optional[Resolver] = None,
    user_agent: str = BROWSER_UA,
    max_redirects: int = MAX_REDIRECTS,
) -> AsyncIterator[httpx.Response]:
    """Stream a GET, following redirects manually with a per-hop SSRF gate.

    Yields the final (non-redirect) response with its body unread, so the
    caller can read headers only or a bounded body slice. The `client` MUST be
    configured with `follow_redirects=False` (we follow manually so every hop
    passes `assert_public_http_url`).

    Raises:
        UnsafeUrlError: any hop (including the first) points at a non-public
            address — propagated so the caller can degrade.
        RedirectLimitError: exceeded `max_redirects`.
    """
    current = url
    for _ in range(max_redirects + 1):
        await assert_public_http_url(current, resolver=resolver)
        async with client.stream("GET", current, headers={"User-Agent": user_agent}) as resp:
            if resp.is_redirect and "location" in resp.headers:
                current = urljoin(current, resp.headers["location"])
                continue
            yield resp
            return
    raise RedirectLimitError(f"exceeded {max_redirects} redirects for {url}")


def _interleave_families(addresses: list[str]) -> list[str]:
    """Alternate address families (first family first), RFC 8305 style.

    Keeps the resolver's preference for the first address, then alternates
    IPv6/IPv4 so one unreachable family does not have to be exhausted before
    the other is tried.
    """
    v6 = [a for a in addresses if ":" in a]
    v4 = [a for a in addresses if ":" not in a]
    first, second = (v6, v4) if addresses and ":" in addresses[0] else (v4, v6)
    out: list[str] = []
    for i in range(max(len(first), len(second))):
        out.extend(group[i] for group in (first, second) if i < len(group))
    return out


class PublicOnlyNetworkBackend(httpcore.AsyncNetworkBackend):
    """httpcore network backend that only ever opens sockets to public addresses.

    Every `connect_tcp` re-resolves the host and refuses (UnsafeUrlError) if
    ANY answer is non-public, then dials a validated address — so what was
    checked is exactly what is connected to. Unix sockets are refused outright.
    `inner` is the real socket backend (injectable for tests).
    """

    def __init__(
        self,
        *,
        resolver: Optional[Resolver] = None,
        inner: Optional[httpcore.AsyncNetworkBackend] = None,
    ) -> None:
        self._resolver = resolver
        # httpcore types AnyIOBackend as a fallback stub when anyio is missing;
        # anyio is an httpx dependency, so the real backend is always there.
        self._inner = inner or cast(httpcore.AsyncNetworkBackend, httpcore.AnyIOBackend())

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: Optional[float] = None,
        local_address: Optional[str] = None,
        socket_options: Optional[Iterable[Any]] = None,
    ) -> httpcore.AsyncNetworkStream:
        # One connect-timeout budget covers resolution AND every dial, as it did
        # inside httpcore's own backend: a resolver an attacker controls must
        # not hang the request past its timeout.
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            with anyio.fail_after(timeout):
                addresses = await resolve_public_addresses(host, port, resolver=self._resolver)
        except TimeoutError as e:
            raise httpcore.ConnectTimeout(f"resolving {host!r} timed out") from e
        addresses = _interleave_families(addresses)
        last_error: Optional[Exception] = None
        for index, address in enumerate(addresses):
            attempt_timeout = None
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                # Split what is left across the addresses still to try, so a
                # black-holed first address (e.g. dead IPv6) cannot eat the whole
                # budget — the replacement for the happy-eyeballs fallback the
                # stock backend did when it resolved the name itself.
                attempt_timeout = remaining / (len(addresses) - index)
            try:
                return await self._inner.connect_tcp(
                    address,
                    port,
                    timeout=attempt_timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as e:
                last_error = e  # try the next validated address
        raise last_error or httpcore.ConnectTimeout(f"connecting to {host!r} timed out")

    async def connect_unix_socket(
        self,
        path: str,
        timeout: Optional[float] = None,
        socket_options: Optional[Iterable[Any]] = None,
    ) -> httpcore.AsyncNetworkStream:
        raise UnsafeUrlError("unix sockets are not a public egress target")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class PublicOnlyTransport(httpx.AsyncHTTPTransport):
    """httpx transport whose connection pool dials through PublicOnlyNetworkBackend.

    Proxies are deliberately unsupported: through a proxy the socket goes to
    the proxy and the proxy does its own resolution, which would bypass the
    pin. (httpx also ignores *_PROXY env vars when a transport is passed in.)
    """

    def __init__(
        self,
        *,
        resolver: Optional[Resolver] = None,
        inner: Optional[httpcore.AsyncNetworkBackend] = None,
        verify: bool = True,
        http2: bool = False,
        limits: httpx.Limits = httpx.Limits(max_connections=100, max_keepalive_connections=20),
        retries: int = 0,
    ) -> None:
        # Deliberately not super().__init__(): it would build a pool (and load
        # the CA bundle) only for it to be replaced. This relies on ``_pool``
        # being httpx.AsyncHTTPTransport's ONLY state (true for httpx 0.28) —
        # re-check this class whenever httpx is upgraded. trust_env=True for the SSL context only, so SSL_CERT_FILE /
        # SSL_CERT_DIR are honoured exactly as by a default client.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(verify=verify, trust_env=True),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            http1=True,
            http2=http2,
            retries=retries,
            network_backend=PublicOnlyNetworkBackend(resolver=resolver, inner=inner),
        )


def egress_transport(**transport_kwargs: Any) -> Optional[httpx.AsyncBaseTransport]:
    """The transport for a request to a user-supplied URL, or None locally.

    Cloud: a PublicOnlyTransport. Local/desktop: None, meaning "let the client
    build its default transport" — the single trusted user may point at
    localhost / the LAN. For SDK clients (openai, anthropic) that accept an
    `http_client`, prefer `egress_client()`.
    """
    if not deployment_mode.is_cloud_mode():
        return None
    return PublicOnlyTransport(**transport_kwargs)


def egress_sdk_http_client(
    factory: Callable[..., httpx.AsyncClient],
) -> Optional[httpx.AsyncClient]:
    """``http_client`` for an LLM SDK (openai / anthropic) whose base_url the user supplied.

    Cloud: ``factory(transport=PublicOnlyTransport(), follow_redirects=False)``
    — pass the SDK's own ``DefaultAsyncHttpxClient`` as `factory` so its
    timeout default is kept (its connection-limit default is not: httpx uses
    the passed transport's pool as-is, i.e. PublicOnlyTransport's limits). The
    SDK default follows redirects, which would let a public endpoint 302 into
    the network.
    Local/desktop: None — keep the SDK default (a single trusted user).
    """
    transport = egress_transport()
    if transport is None:
        return None
    return factory(transport=transport, follow_redirects=False)


def egress_client(**client_kwargs: Any) -> httpx.AsyncClient:
    """An httpx.AsyncClient for server-side requests to user-supplied URLs.

    Pinned (connect-time SSRF gate) in cloud, a plain client locally. Accepts
    the usual AsyncClient kwargs (timeout, headers, follow_redirects, ...).
    Rejected: `transport` / `proxy` / `mounts` (would replace or bypass the
    pin) and `verify` / `cert` / `http1` / `http2` / `limits` — httpx only
    applies those to a transport it builds itself, so they would take effect
    locally and be silently dropped in cloud.
    """
    for forbidden in ("transport", "proxy", "mounts", "verify", "cert", "http1", "http2", "limits"):
        if forbidden in client_kwargs:
            raise TypeError(f"egress_client does not accept {forbidden!r}")
    transport = egress_transport()
    if transport is None:
        return httpx.AsyncClient(**client_kwargs)
    return httpx.AsyncClient(transport=transport, **client_kwargs)


class PublicOnlyResolver(AbstractResolver):
    """aiohttp resolver that only ever answers with public addresses.

    aiohttp's TCPConnector dials the addresses its resolver returns (TLS still
    uses the hostname for SNI and certificate checks), so validating here pins
    the connection the same way PublicOnlyNetworkBackend does for httpx. A
    literal-IP host never reaches a resolver in aiohttp; literals cannot
    rebind, so the caller's pre-check (`assert_public_http_url[_in_cloud]`)
    is the gate for those.
    """

    def __init__(self, *, resolver: Optional[Resolver] = None) -> None:
        self._resolver = resolver

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        addresses = await resolve_public_addresses(host, port, resolver=self._resolver)
        results: list[ResolveResult] = []
        for address in addresses:
            # resolve_public_addresses already proved every entry parses.
            addr_family = socket.AF_INET6 if ipaddress.ip_address(address).version == 6 else socket.AF_INET
            if family not in (socket.AF_UNSPEC, addr_family):
                continue
            results.append(
                ResolveResult(
                    hostname=host,
                    host=address,
                    port=port,
                    family=addr_family,
                    proto=0,
                    flags=socket.AI_NUMERICHOST,
                )
            )
        if not results:
            raise UnsafeUrlError(f"host {host!r} has no public address in the requested family")
        return results

    async def close(self) -> None:
        return None


def egress_connector(**connector_kwargs: Any) -> aiohttp.TCPConnector:
    """A TCPConnector for aiohttp requests to user-supplied URLs.

    Pinned through PublicOnlyResolver in cloud, aiohttp's default resolver
    locally. Accepts the usual TCPConnector kwargs (ssl, limit, ...);
    `resolver` is rejected because it would replace the pin.
    """
    if "resolver" in connector_kwargs:
        raise TypeError("egress_connector does not accept 'resolver'")
    if not deployment_mode.is_cloud_mode():
        return aiohttp.TCPConnector(**connector_kwargs)
    return aiohttp.TCPConnector(resolver=PublicOnlyResolver(), **connector_kwargs)
