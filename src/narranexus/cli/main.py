"""
@file_name: main.py
@author: Bin Liang
@date: 2026-09-03
@description: ``narranexus plugin new|link|install|list|enable|disable|uninstall|doctor|bisect|publish-check`` and ``narranexus docs gen``.

Every mutating verb goes through the same kernel objects the factory API
uses (``Installer`` / ``RegistryStore`` / ``Bisect``), so the CLI and the
UI can never disagree about state. ``doctor`` is read-only: it validates
every registered plugin against this host and prints the boot decisions
without booting. ``publish-check`` runs the release checklist on a plugin
directory (manifest, versions.json, dist bundle, README, tests) and exits
non-zero on any miss.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

from narranexus.contracts import ManifestError

TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "templates"


def _store():
    from narranexus.kernel.plugins.lifecycle import RegistryStore
    from narranexus.kernel.plugins.paths import registry_path

    return RegistryStore(path=registry_path())


def _print(obj: Any, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, sort_keys=True, default=str))
    elif isinstance(obj, list):
        for row in obj:
            print(row if isinstance(row, str) else json.dumps(row, default=str))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            print(f"{k}: {v}")
    else:
        print(obj)


# ------------------------------------------------------------------ verbs


def cmd_new(args: argparse.Namespace) -> int:
    from narranexus.kernel.plugins.manifest import PLUGIN_ID_RE

    if not PLUGIN_ID_RE.match(args.id) or args.id.startswith("builtin."):
        print(f"invalid plugin id {args.id!r} (expected <publisher>.<name>, not builtin.)", file=sys.stderr)
        return 2
    dest = Path(args.dir or args.id).resolve()
    if dest.exists() and any(dest.iterdir()):
        print(f"{dest} exists and is not empty", file=sys.stderr)
        return 2
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    missing = [k for k in kinds if not (TEMPLATES_DIR / k).is_dir()]
    if missing:
        print(f"unknown template kind(s) {missing}; available: {sorted(p.name for p in TEMPLATES_DIR.iterdir() if p.is_dir())}", file=sys.stderr)
        return 2
    from narranexus.cli.scaffold import scaffold

    written = scaffold(args.id, kinds, dest, display_name=args.display_name or args.id.split(".", 1)[1].replace("_", " ").title())
    print(f"created {dest} with {len(written)} files for kinds {kinds}")
    return 0


def cmd_link(args: argparse.Namespace) -> int:
    from narranexus.kernel.plugins.install import Installer, LocalSource

    result = Installer(store=_store()).install(LocalSource(Path(args.path), mode="link"), installed_by="cli", replace=args.force)
    print(f"linked {result.plugin_id} {result.version} at {result.path}; restart the app to load it")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    from narranexus.kernel.plugins.install import Installer

    result = Installer(store=_store()).install(args.source, installed_by="cli", permissions_acknowledged=args.yes, replace=args.force)
    perms = {k: v for k, v in result.permissions.items() if v}
    print(f"installed {result.plugin_id} {result.version} ({result.mode}) at {result.path}")
    if result.warnings:
        print("warnings: " + "; ".join(result.warnings))
    if perms and not args.yes:
        print(f"declared permissions: {json.dumps(perms)} — acknowledge with `narranexus plugin enable {result.plugin_id} --ack`")
    print("restart the app to load it")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    reg = _store().read()
    rows = [
        {"id": pid, "version": r.installed_version, "enabled": r.enabled, "state": r.state, "mode": r.mode, "scope": r.scope, "path": r.path, "error": r.last_error}
        for pid, r in sorted(reg.plugins.items())
    ]
    if args.json:
        _print({"plugins": rows, "safe_mode": reg.safe_mode, "safe_mode_reason": reg.safe_mode_reason}, as_json=True)
        return 0
    if reg.safe_mode:
        print(f"SAFE MODE: {reg.safe_mode_reason}")
    if not rows:
        print("no user plugins")
    for r in rows:
        flag = "on " if r["enabled"] else "off"
        print(f"{flag} {r['id']:<32} {r['version']:<10} {r['state']:<14} {r['mode']:<5} {r['scope']}  {r['error'] or ''}")
    return 0


def _toggle_builtin(plugin_id: str, enabled: bool) -> bool:
    """builtin.* ids are toggled through registry.json builtin_overrides; returns False for non-builtins."""
    if not plugin_id.startswith("builtin."):
        return False
    from narranexus.kernel.plugins.builtins import builtin_manifests

    manifest = next((m for m in builtin_manifests() if m.id == plugin_id), None)
    if manifest is None:
        raise KeyError(f"{plugin_id} is not a builtin plugin")
    if manifest.protected and not enabled:
        raise PermissionError(f"{plugin_id} is protected and cannot be disabled")

    def _mutate(reg):
        if enabled:
            reg.builtin_overrides.pop(plugin_id, None)
        else:
            reg.builtin_overrides[plugin_id] = {"enabled": False}

    _store().update(_mutate)
    print(f"{plugin_id}: builtin {'enabled' if enabled else 'disabled'}; restart the app")
    return True


def cmd_enable(args: argparse.Namespace) -> int:
    if _toggle_builtin(args.id, True):
        return 0
    store = _store()
    rec = store.set_enabled(args.id, True)
    if args.ack:
        def _mutate(reg):
            reg.plugins[args.id].permissions_acknowledged = True

        store.update(_mutate)
    print(f"{args.id}: enabled (state {rec.state}); restart the app")
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    if _toggle_builtin(args.id, False):
        return 0
    rec = _store().set_enabled(args.id, False)
    print(f"{args.id}: disabled (state {rec.state}); restart the app")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    from narranexus.kernel.plugins.install import Installer

    Installer(store=_store()).uninstall(args.id, purge_files=not args.keep_files)
    print(f"{args.id}: uninstalled")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from narranexus.kernel.plugins.compat import host_version
    from narranexus.kernel.plugins.loader import discover, plan_load

    store = _store()
    found = discover(cloud=False, user_registry_path=store.path, host_version=host_version())
    users = [m for m in found.manifests if not m.is_builtin]
    plan = plan_load(found.manifests)
    report = {
        "host_version": host_version(),
        "safe_mode": found.safe_mode,
        "would_load": [m.id for m in plan.ordered if not m.is_builtin],
        "blocked": plan.blocked,
        "rejected": found.rejected,
        "builtins": [m.id for m in found.manifests if m.is_builtin],
    }
    _print(report, as_json=args.json)
    return 1 if (found.rejected or plan.blocked) and not users else 0


def cmd_bisect(args: argparse.Namespace) -> int:
    from narranexus.kernel.plugins.bisect import Bisect

    b = Bisect(_store())
    if args.action == "start":
        step = b.start()
    elif args.action == "good":
        step = b.answer(good=True)
    elif args.action == "bad":
        step = b.answer(good=False)
    else:
        b.stop()
        print("bisect stopped; cleared plugins re-enabled")
        return 0
    if step.culprit:
        print(f"culprit: {step.culprit} (kept disabled) — run `narranexus plugin bisect stop`")
    else:
        print(f"{step.remaining} suspects; enabled for the next start: {', '.join(step.trial) or '-'}; restart, then answer good|bad")
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    reg = _store().rollback_to_lkg()
    print(f"registry restored from last-known-good ({len(reg.plugins)} plugins); restart the app")
    return 0


def cmd_publish_check(args: argparse.Namespace) -> int:
    from narranexus.cli.publish_check import publish_check

    problems = publish_check(Path(args.path))
    for p in problems:
        print(f"- {p}")
    print("ok" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


def cmd_docs_gen(args: argparse.Namespace) -> int:
    import subprocess

    root = Path(__file__).resolve().parents[3]
    rc = 0
    for script in ("scripts/dev/gen_plugin_docs.py", "scripts/dev/gen_theme_tokens.py"):
        cmd = [sys.executable, str(root / script)] + (["--write"] if args.write else [])
        rc |= subprocess.call(cmd)
    return rc


# ------------------------------------------------------------------ parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="narranexus", description="NarraNexus plugin tools")
    sub = parser.add_subparsers(dest="command", required=True)

    plugin = sub.add_parser("plugin", help="manage user plugins")
    psub = plugin.add_subparsers(dest="verb", required=True)

    p = psub.add_parser("new", help="scaffold a plugin from templates")
    p.add_argument("id")
    p.add_argument("--kinds", default="routes", help="comma-separated template kinds")
    p.add_argument("--dir", default=None)
    p.add_argument("--display-name", default=None)
    p.set_defaults(fn=cmd_new)

    p = psub.add_parser("link", help="register a local plugin directory in place")
    p.add_argument("path")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_link)

    p = psub.add_parser("install", help="install from owner/repo[@tag], owner/repo#ref, or a path")
    p.add_argument("source")
    p.add_argument("--yes", action="store_true", help="acknowledge declared permissions")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_install)

    p = psub.add_parser("list")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_list)

    p = psub.add_parser("enable")
    p.add_argument("id")
    p.add_argument("--ack", action="store_true", help="acknowledge declared permissions")
    p.set_defaults(fn=cmd_enable)

    p = psub.add_parser("disable")
    p.add_argument("id")
    p.set_defaults(fn=cmd_disable)

    p = psub.add_parser("uninstall")
    p.add_argument("id")
    p.add_argument("--keep-files", action="store_true")
    p.set_defaults(fn=cmd_uninstall)

    p = psub.add_parser("doctor", help="validate every registered plugin against this host")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_doctor)

    p = psub.add_parser("bisect")
    p.add_argument("action", choices=["start", "good", "bad", "stop"])
    p.set_defaults(fn=cmd_bisect)

    p = psub.add_parser("rollback", help="restore registry.json from last-known-good")
    p.set_defaults(fn=cmd_rollback)

    p = psub.add_parser("publish-check", help="release checklist for a plugin directory")
    p.add_argument("path", nargs="?", default=".")
    p.set_defaults(fn=cmd_publish_check)

    docs = sub.add_parser("docs", help="generated documentation")
    dsub = docs.add_subparsers(dest="verb", required=True)
    p = dsub.add_parser("gen")
    p.add_argument("--write", action="store_true")
    p.set_defaults(fn=cmd_docs_gen)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    fn: Callable[[argparse.Namespace], int] = args.fn
    try:
        return fn(args)
    except (ManifestError, FileNotFoundError, KeyError, PermissionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — a CLI prints, it does not traceback
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
