"""
@file_name: policy.py
@author:
@date: 2026-09-22
@description: Per-origin permission model for the in-app browser (design §6).

Ordinary HTTP(S) browsing has no permission policy. This model only decides
independent privileged capabilities; website access rules do not exist.

``full_cdp_access`` is deliberately outside that flow: it is the whole browser
(arbitrary protocol commands, every origin, the filesystem via downloads), so
it is deny-by-default and **not grantable through the routine prompt**. Making
it a yes/no dialog would train users to click through the one permission that
has no ceiling.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal, Optional
from urllib.parse import urlparse

#: What a policy says about one capability at one origin.
Verdict = Literal["allow", "deny", "ask"]

#: The capabilities decided independently per origin.
Capability = Literal["downloads", "uploads", "full_cdp_access", "auto_review"]

#: How long an approval lasts. ``turn`` = this agent turn only; ``thread`` =
#: the whole conversation.
Lifetime = Literal["turn", "thread"]

_VERDICTS = ("allow", "deny", "ask")

#: Built-in fallbacks, used when neither the origin entry nor the default
#: policy says anything. Browsing needs no per-site approval; file movement
#: and arbitrary scripts keep their independent restrictions.
_BUILTIN: dict[str, Verdict] = {
    "downloads": "ask",
    "uploads": "ask",
    "full_cdp_access": "deny",
    "auto_review": "allow",
}

#: Capabilities the routine approval prompt may never grant.
_NOT_GRANTABLE = frozenset({"full_cdp_access"})


@dataclass(frozen=True)
class OriginPolicy:
    """Per-origin verdicts. ``None`` means "inherit"."""

    downloads: Optional[Verdict] = None
    uploads: Optional[Verdict] = None
    full_cdp_access: Optional[Verdict] = None
    auto_review: Optional[Verdict] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @staticmethod
    def from_dict(raw: dict) -> "OriginPolicy":
        """Build from config, ignoring unknown keys but validating known ones.

        Only supported capabilities enter the runtime model. Invalid verdicts
        for supported capabilities are errors, never implicit defaults.
        """
        known = {f for f in OriginPolicy.__dataclass_fields__}
        kwargs = {}
        for key, value in (raw or {}).items():
            if key not in known:
                continue
            if value not in _VERDICTS:
                raise ValueError(f"invalid verdict {value!r} for {key}")
            kwargs[key] = value
        return OriginPolicy(**kwargs)


@dataclass(frozen=True)
class PolicyDecision:
    """The answer, plus enough context for a prompt or an audit row."""

    verdict: Verdict
    capability: str
    origin: Optional[str]
    matched: str
    reason: str


@dataclass
class BrowserPolicy:
    """A whole agent's browser permissions, plus its live approvals."""

    default_origin_policy: OriginPolicy = field(default_factory=OriginPolicy)
    origins: dict[str, OriginPolicy] = field(default_factory=dict)
    allow_history_access: bool = False
    #: (origin, capability, scope_key) for approvals granted this session.
    _grants: set[tuple[str, str, str]] = field(default_factory=set, repr=False)
    _denials: set[tuple[str, str, str]] = field(default_factory=set, repr=False)
    _approval_receipts: set[str] = field(default_factory=set, repr=False)

    def default_verdict(self, capability: str) -> Verdict:
        """Share configured and built-in defaults with the settings view."""
        return getattr(self.default_origin_policy, capability) or _BUILTIN[capability]

    # ── approvals ────────────────────────────────────────────────────────

    def grant(
        self,
        *,
        origin: str,
        capability: Capability,
        lifetime: Lifetime,
        turn_id: str,
        thread_id: str,
        verdict: Verdict = "allow",
    ) -> None:
        """Record a user approval. Ignored for non-grantable capabilities.

        Silently ignoring rather than raising: the caller is a UI click
        handler, and the guarantee we owe is that the grant has no effect —
        which ``decide`` enforces regardless of what is stored here.
        """
        if capability in _NOT_GRANTABLE:
            return
        if capability not in _BUILTIN or lifetime not in ("turn", "thread"):
            raise ValueError("invalid approval capability or lifetime")
        identity = turn_id if lifetime == "turn" else thread_id
        if not identity:
            raise ValueError("approval scope is missing")
        scope = f"{lifetime}:{identity}"
        key = (origin, capability, scope)
        self._grants.discard(key)
        self._denials.discard(key)
        (self._grants if verdict == "allow" else self._denials).add(key)

    def _has_grant(
        self, *, origin: str, capability: str, turn_id: Optional[str], thread_id: Optional[str]
    ) -> bool:
        if capability in _NOT_GRANTABLE:
            return False
        if turn_id and (origin, capability, f"turn:{turn_id}") in self._grants:
            return True
        if thread_id and (origin, capability, f"thread:{thread_id}") in self._grants:
            return True
        return False

    def clear_turn_grants(self, turn_id: str) -> None:
        """Drop approvals scoped to a finished turn."""
        self._grants = {g for g in self._grants if g[2] != f"turn:{turn_id}"}
        self._denials = {g for g in self._denials if g[2] != f"turn:{turn_id}"}

    # ── serialisation ────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """The whole document, grants included.

        Grants are persisted — not for durability, but because the session
        that must honour them runs in the MCP host process while the approval
        is applied in the backend process. A grant kept only in memory is
        invisible to the side that needs it: the user clicks allow and the
        agent stays refused (observed live 2026-09-22).
        """
        return {
            "allow_history_access": self.allow_history_access,
            "default_origin_policy": self.default_origin_policy.to_dict(),
            "origins": {k: v.to_dict() for k, v in self.origins.items()},
            "grants": sorted([list(g) for g in self._grants]),
            "denials": sorted([list(g) for g in self._denials]),
            "approval_receipts": sorted(self._approval_receipts),
        }

    @staticmethod
    def from_dict(raw: dict) -> "BrowserPolicy":
        raw = raw or {}
        return BrowserPolicy(
            default_origin_policy=OriginPolicy.from_dict(raw.get("default_origin_policy") or {}),
            origins={
                k: entry for k, v in (raw.get("origins") or {}).items()
                if (entry := OriginPolicy.from_dict(v or {})).to_dict()
            },
            allow_history_access=bool(raw.get("allow_history_access", False)),
            # Tolerated as absent: rows written before grants were persisted
            # must still load.
            _grants={
                (g[0], g[1], g[2])
                for g in (raw.get("grants") or [])
                if isinstance(g, (list, tuple)) and len(g) == 3 and all(isinstance(v, str) for v in g)
                and g[1] in _BUILTIN
            },
            _denials={tuple(g) for g in raw.get("denials", [])
                      if isinstance(g, (list, tuple)) and len(g) == 3 and all(isinstance(v, str) for v in g)
                      and g[1] in _BUILTIN},
            _approval_receipts={value for value in raw.get("approval_receipts", []) if isinstance(value, str)},
        )

    def with_origin(self, origin: str, policy: OriginPolicy) -> "BrowserPolicy":
        """Return a copy with one origin replaced. Grants are preserved."""
        merged = dict(self.origins)
        merged[origin] = policy
        return replace(self, origins=merged)


def origin_of(url: str) -> Optional[str]:
    """``scheme://host[:port]`` for http(s) URLs, else None.

    Anything that is not http(s) never resolves to an origin we can grant:
    ``file:`` turns a navigation into a local-file read and ``javascript:``
    into script injection, and neither is something a per-site prompt can
    meaningfully describe to a user.

    The default port is dropped so ``https://x`` and ``https://x:443`` are the
    same grant — otherwise a user who approved one would be re-prompted for
    the other, and re-prompting is what trains people to click yes.
    """
    try:
        parsed = urlparse((url or "").strip())
        port = parsed.port
    except (ValueError, TypeError, AttributeError):
        return None
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    if ":" in host:
        host = f"[{host}]"
    default_port = 443 if scheme == "https" else 80
    if port is None or port == default_port:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def _match_origin(policy: BrowserPolicy, origin: str) -> tuple[Optional[OriginPolicy], str]:
    """Exact entry, then wildcard host, then nothing. Exact always wins."""
    exact = policy.origins.get(origin)
    if exact is not None:
        return exact, origin

    scheme, _, host = origin.partition("://")
    for key, entry in policy.origins.items():
        key_scheme, _, key_host = key.partition("://")
        if key_scheme != scheme or not key_host.startswith("*."):
            continue
        suffix = key_host[1:]  # ".github.com"
        # Require a real label boundary: `evil-github.com` must not match, and
        # neither must the bare domain — `*.x` and `x` are different grants.
        if host.endswith(suffix) and len(host) > len(suffix):
            return entry, key
    return None, "default"


def decide(
    policy: BrowserPolicy,
    *,
    url: str,
    capability: Capability,
    turn_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> PolicyDecision:
    """Decide one capability at one URL. Pure; unit-tested per branch.

    Resolution order, and why:

    1. **Scheme** — non-http(s) is denied outright, before any lookup.
    2. **Explicit deny** — a configured ban is final. An approval prompt must
       never be a route around it, because that is precisely the escalation a
       hostile page would aim for.
    3. **Live grant** — what the user already approved, within its lifetime.
    4. **Origin entry**, then **default policy**, then the built-in fallback.
    """
    origin = origin_of(url)
    if origin is None:
        return PolicyDecision(
            verdict="deny",
            capability=capability,
            origin=None,
            matched="none",
            reason="unsupported-scheme",
        )

    entry, matched = _match_origin(policy, origin)

    configured: Optional[Verdict] = getattr(entry, capability, None) if entry else None
    if configured is None:
        configured = getattr(policy.default_origin_policy, capability, None)

    if configured == "deny":
        return PolicyDecision(
            verdict="deny",
            capability=capability,
            origin=origin,
            matched=matched,
            reason="configured-deny",
        )

    if any((origin, capability, f"{kind}:{value}") in policy._denials
           for kind, value in (("turn", turn_id), ("thread", thread_id)) if value):
        return PolicyDecision(verdict="deny", capability=capability, origin=origin,
                              matched=matched, reason="user-denied")

    if policy._has_grant(  # noqa: SLF001 - same module's private state
        origin=origin, capability=capability, turn_id=turn_id, thread_id=thread_id
    ):
        return PolicyDecision(
            verdict="allow",
            capability=capability,
            origin=origin,
            matched=matched,
            reason="granted",
        )

    if configured is not None:
        return PolicyDecision(
            verdict=configured,
            capability=capability,
            origin=origin,
            matched=matched,
            reason="configured",
        )

    return PolicyDecision(
        verdict=_BUILTIN[capability],
        capability=capability,
        origin=origin,
        matched=matched,
        reason="builtin-default",
    )
