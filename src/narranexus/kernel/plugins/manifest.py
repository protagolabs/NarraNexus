"""
@file_name: manifest.py
@author: Bin Liang
@date: 2026-09-03
@description: ``narranexus-plugin.json`` — the declarative description of a plugin, validated strictly.

The manifest is the whole plugin as far as the loader is concerned at startup:
identity, compatibility, what slots it provides (``provides``), what slots it
declares for others (``declares``), which child slots it keeps alive when it
replaces a composite one (``redeclares``), dependencies, hosts, activation
events, permissions, install policy. Code is only imported when an activation
event fires, so everything the platform needs to render UI, plan loading or
reject a plugin must be here.

Validation is strict (unknown keys are errors) and slot-aware: every
``provides`` key must be a declared slot path, and the value shape must match
the slot's arity. Batch 0 validates builtin manifests only; user manifests
arrive with the plugin factory in batch 2.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from narranexus.contracts import API_VERSIONS, ManifestError, Stability
from narranexus.kernel.plugins.compat import Range, Version
from narranexus.kernel.plugins.slots import Slot, SlotTree, validate_path

PLUGIN_ID_RE = re.compile(r"^[a-z0-9_-]+(\.[a-z0-9_-]+)+$")
SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$")
BUILTIN_PREFIX = "builtin."

Host = Literal["backend", "mcp", "workers", "frontend"]
ALL_HOSTS: tuple[Host, ...] = ("backend", "mcp", "workers", "frontend")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Publisher(_Strict):
    name: str
    url: str = ""


class BackendSpec(_Strict):
    package: str = "backend"
    pip: tuple[str, ...] = ()
    activate: bool = False


class FrontendSpec(_Strict):
    entry: str
    locales: str = ""
    integrity: str = ""


class Permissions(_Strict):
    network: tuple[str, ...] = ()
    filesystem: tuple[str, ...] = ()
    subprocess: bool = False
    env: tuple[str, ...] = ()


class InstallSpec(_Strict):
    deps: Literal["eager", "on_demand"] = "eager"


class SizeHint(_Strict):
    backend_deps_mb: float = 0
    frontend_kb: float = 0


class SlotDeclaration(_Strict):
    arity: Literal["one", "many"]
    contract: str
    default: str | None = None
    distribution_only: bool = False
    stability: Literal["alpha", "beta", "stable"] = "alpha"
    doc: str = ""

    @field_validator("contract")
    @classmethod
    def _contract_symbol(cls, v: str) -> str:
        if not SYMBOL_RE.match(v):
            raise ValueError(f"contract must be 'module.path:Symbol', got {v!r}")
        return v


class Manifest(_Strict):
    """Validated ``narranexus-plugin.json``."""

    id: str
    version: str
    display_name: str = Field(alias="displayName")
    description: str = ""
    publisher: Publisher | None = None
    license: str = ""
    min_app_version: str = Field(default="0.0.0", alias="minAppVersion")
    api: Mapping[str, int] = Field(default_factory=dict)
    dependencies: Mapping[str, str] = Field(default_factory=dict)
    after_dependencies: tuple[str, ...] = Field(default=(), alias="afterDependencies")
    # Empty means "every host"; the loader treats it as ALL_HOSTS.
    hosts: tuple[Host, ...] = ()
    backend: BackendSpec | None = None
    frontend: FrontendSpec | None = None
    provides: Mapping[str, str | tuple[str, ...]] = Field(default_factory=dict)
    declares: Mapping[str, SlotDeclaration] = Field(default_factory=dict)
    redeclares: tuple[str, ...] = ()
    activation_events: tuple[str, ...] = Field(default=(), alias="activationEvents")
    permissions: Permissions = Field(default_factory=Permissions)
    install: InstallSpec = Field(default_factory=InstallSpec)
    size: SizeHint = Field(default_factory=SizeHint)
    quality: Literal["bronze", "silver", "gold"] = "bronze"
    protected: bool = False
    distribution_only: bool = Field(default=False, alias="distributionOnly")

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    # ---------------------------------------------------------- validators

    @field_validator("id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not PLUGIN_ID_RE.match(v):
            raise ValueError(f"id must be '<publisher>.<name>' in [a-z0-9_.-], got {v!r}")
        return v

    @field_validator("version", "min_app_version")
    @classmethod
    def _semver(cls, v: str) -> str:
        Version.parse(v)
        return v

    @field_validator("dependencies")
    @classmethod
    def _dependency_ranges(cls, v: Mapping[str, str]) -> Mapping[str, str]:
        for dep_id, rng in v.items():
            if not PLUGIN_ID_RE.match(dep_id):
                raise ValueError(f"dependency id {dep_id!r} is not a plugin id")
            Range.parse(rng)
        return dict(v)

    @field_validator("provides")
    @classmethod
    def _provides_shape(cls, v: Mapping[str, Any]) -> Mapping[str, str | tuple[str, ...]]:
        out: dict[str, str | tuple[str, ...]] = {}
        for path, value in v.items():
            validate_path(path)
            if isinstance(value, str):
                if not SYMBOL_RE.match(value):
                    raise ValueError(f"provides[{path!r}] must be 'module.path:Symbol', got {value!r}")
                out[path] = value
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if not isinstance(item, str) or not SYMBOL_RE.match(item):
                        raise ValueError(f"provides[{path!r}] items must be 'module.path:Symbol', got {item!r}")
                out[path] = tuple(value)
            else:
                raise ValueError(f"provides[{path!r}] must be a symbol or a list of symbols")
        return out

    @field_validator("declares")
    @classmethod
    def _declares_paths(cls, v: Mapping[str, SlotDeclaration]) -> Mapping[str, SlotDeclaration]:
        for path in v:
            validate_path(path)
        return dict(v)

    @field_validator("redeclares", "after_dependencies")
    @classmethod
    def _paths_or_ids(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(v)

    # ------------------------------------------------------------ helpers

    @property
    def is_builtin(self) -> bool:
        return self.id.startswith(BUILTIN_PREFIX)

    @property
    def semantic_version(self) -> Version:
        return Version.parse(self.version)

    def effective_hosts(self) -> tuple[Host, ...]:
        """``hosts`` with the empty tuple meaning every host."""
        return self.hosts or ALL_HOSTS

    def provided_slots(self) -> tuple[str, ...]:
        return tuple(self.provides)

    def declared_slots(self, tree_owner: str | None = None) -> tuple[Slot, ...]:
        """The ``Slot`` objects this plugin declares (owner = this plugin)."""
        return tuple(
            Slot(
                path=path,
                arity=decl.arity,
                contract=decl.contract,
                owner=tree_owner or self.id,
                default=decl.default,
                distribution_only=decl.distribution_only,
                stability=Stability(decl.stability),
                doc=decl.doc,
            )
            for path, decl in self.declares.items()
        )


# =============================================================================
# Parsing entry points (slot-aware)
# =============================================================================


def parse_manifest(
    data: Mapping[str, Any],
    *,
    tree: SlotTree,
    allow_builtin: bool = False,
    host_version: str | None = None,
) -> Manifest:
    """Validate ``data`` against the schema and the slot tree.

    Raises ``ManifestError`` with a message that names the offending field.
    """
    try:
        manifest = Manifest.model_validate(dict(data))
    except ValidationError as exc:
        raise ManifestError(f"invalid manifest: {_format_validation_error(exc)}") from None

    if manifest.is_builtin and not allow_builtin:
        raise ManifestError(f"{manifest.id}: the 'builtin.' prefix is reserved for the host's own plugins")

    _check_declares_in_own_namespace(manifest)
    _check_provides_against_tree(manifest, tree)
    _check_redeclares(manifest, tree)
    _check_api_versions(manifest)
    if host_version is not None:
        _check_min_app_version(manifest, host_version)
    return manifest


def load_manifest(path: Path, *, tree: SlotTree, allow_builtin: bool = False, host_version: str | None = None) -> Manifest:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"{path}: cannot read manifest: {exc}") from None
    return parse_manifest(data, tree=tree, allow_builtin=allow_builtin, host_version=host_version)


def derive_activation_events(manifest: Manifest) -> tuple[str, ...]:
    """Explicit events plus the ones implied by ``provides`` (VS Code 1.74 rule).

    Batch 0 knows two implications: any UI page/panel slot implies
    ``onPage:<id>`` / ``onPanel:<id>``; any other provided slot implies
    ``onStartup`` (declarative registration needs the symbols at boot).
    """
    events: list[str] = list(manifest.activation_events)
    for path in manifest.provides:
        if path == "ui.pages" or path.startswith("ui.pages."):
            events.append(f"onPage:{manifest.id}")
        elif path == "ui.panels" or path.startswith("ui.panels."):
            events.append(f"onPanel:{manifest.id}")
        elif "onStartup" not in events:
            events.append("onStartup")
    return tuple(dict.fromkeys(events))


# =============================================================================
# Checks
# =============================================================================


def _check_provides_against_tree(manifest: Manifest, tree: SlotTree) -> None:
    declared_here = set(manifest.declares)
    for path, value in manifest.provides.items():
        slot = tree.try_get(path)
        if slot is None and path not in declared_here:
            raise ManifestError(
                f"{manifest.id}: provides[{path!r}] is not a declared slot. Known: {list(tree.paths())}"
            )
        arity = slot.arity if slot is not None else manifest.declares[path].arity
        if arity == "one" and not isinstance(value, str):
            raise ManifestError(f"{manifest.id}: provides[{path!r}] fills a one-arity slot; give a single symbol")
        if arity == "many" and isinstance(value, str):
            raise ManifestError(f"{manifest.id}: provides[{path!r}] fills a many-arity slot; give a list of symbols")
        if slot is not None and slot.distribution_only and not manifest.distribution_only:
            raise ManifestError(
                f"{manifest.id}: provides[{path!r}] is distribution-only; the manifest must set distributionOnly"
            )


def _check_declares_in_own_namespace(manifest: Manifest) -> None:
    """A plugin may declare slots under its own id or under a composite slot it provides.

    Own namespace (``acme.weather.sources``) keeps third-party extension points
    attributable, and its missing ancestors are safe to auto-create. Under a
    provided composite (``builtin.turn`` provides ``turn.pipeline`` and declares
    ``turn.pipeline.recall``) is the ownership rule of the slot tree itself:
    the provider of a composite owns its children.
    """
    provided = set(manifest.provides)
    for path in manifest.declares:
        own = path != manifest.id and path.startswith(manifest.id + ".")
        under_provided = any(path.startswith(p + ".") for p in provided)
        if not (own or under_provided):
            raise ManifestError(
                f"{manifest.id}: declares[{path!r}] must live under this plugin's own namespace "
                f"({manifest.id!r}.<name>) or under a composite slot this plugin provides"
            )


def _check_redeclares(manifest: Manifest, tree: SlotTree) -> None:
    provided = set(manifest.provides)
    for path in manifest.redeclares:
        validate_path(path)
        parent = path.rpartition(".")[0]
        if not any(parent == p or parent.startswith(p + ".") for p in provided):
            raise ManifestError(
                f"{manifest.id}: redeclares[{path!r}] must be a descendant of a slot this plugin provides"
            )
        if path not in tree:
            raise ManifestError(f"{manifest.id}: redeclares[{path!r}] is not a known slot")


def _check_api_versions(manifest: Manifest) -> None:
    for kind, wanted in manifest.api.items():
        have = API_VERSIONS.get(kind)
        if have is None:
            raise ManifestError(f"{manifest.id}: api[{kind!r}] is not a contract kind. Known: {sorted(API_VERSIONS)}")
        if wanted != have:
            # A contract version bump is by definition a breaking change
            # (docs/API_POLICY.md §5), so any mismatch fails closed.
            raise ManifestError(
                f"{manifest.id}: api[{kind!r}] wants {wanted} but this host provides {have} (versions must match)"
            )


def _check_min_app_version(manifest: Manifest, host_version: str) -> None:
    if Version.parse(host_version) < Version.parse(manifest.min_app_version):
        raise ManifestError(
            f"{manifest.id}: minAppVersion {manifest.min_app_version} exceeds host {host_version}"
        )


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "<root>"
        parts.append(f"{loc}: {err.get('msg')}")
    return "; ".join(parts)


__all__ = [
    "ALL_HOSTS",
    "BUILTIN_PREFIX",
    "Host",
    "Manifest",
    "Permissions",
    "SlotDeclaration",
    "derive_activation_events",
    "load_manifest",
    "parse_manifest",
]
