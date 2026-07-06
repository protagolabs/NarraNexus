"""
@file_name: test_trigger_startup_alignment.py
@author: Bin Liang
@date: 2026-07-02
@description: Guard test — every channel trigger entrypoint must be wired into
              every local startup path (CLAUDE.md rule #7).

The recurring outage class: a channel module ships a ``run_*_trigger.py``
long-poll/WS entrypoint, but one of the startup paths never launches it, so
binding succeeds while inbound messages silently never arrive. It bit
Slack/Telegram (dev-local.sh only), then NarraMessenger (compose gap), then
WeChat (missing from the Tauri desktop factories entirely).

Source of truth is the filesystem: ``module/*_module/run_*_trigger.py``.
Every discovered entrypoint must appear in:

  - ``run.sh``                        (bash local mode)
  - ``scripts/dev-local.sh``          (tmux dev mode)
  - ``tauri/src-tauri/src/state.rs``  (desktop dmg — BOTH service factories,
                                       ``bundled_services`` and ``dev_services``)

The cloud counterpart (``NarraNexus-deploy/stacks/narranexus-app/compose.yml``)
lives in a separate repo and is guarded by that repo's own check script.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

MODULE_DIR = REPO_ROOT / "src" / "xyz_agent_context" / "module"

STARTUP_FILES = {
    "run.sh": REPO_ROOT / "run.sh",
    "scripts/dev-local.sh": REPO_ROOT / "scripts" / "dev-local.sh",
    "tauri/src-tauri/src/state.rs": REPO_ROOT / "tauri" / "src-tauri" / "src" / "state.rs",
}


def discover_trigger_entrypoints() -> list[str]:
    """Dotted module path of every ``module/*_module/run_*_trigger.py``."""
    return sorted(
        f"xyz_agent_context.module.{path.parent.name}.{path.stem}"
        for path in MODULE_DIR.glob("*_module/run_*_trigger.py")
    )


def test_trigger_entrypoints_exist():
    """Sanity: discovery finds the known channel triggers (guards the glob)."""
    entrypoints = discover_trigger_entrypoints()
    assert "xyz_agent_context.module.lark_module.run_lark_trigger" in entrypoints
    assert "xyz_agent_context.module.wechat_module.run_wechat_trigger" in entrypoints
    assert len(entrypoints) >= 6


def test_every_trigger_wired_into_every_startup_path():
    missing: list[str] = []
    entrypoints = discover_trigger_entrypoints()
    for label, path in STARTUP_FILES.items():
        text = path.read_text(encoding="utf-8")
        for entrypoint in entrypoints:
            if entrypoint not in text:
                missing.append(f"{label}: {entrypoint}")
    assert not missing, (
        "Channel trigger entrypoints not launched by every startup path "
        "(CLAUDE.md rule #7 — a bound channel silently receives nothing "
        "where the trigger is missing):\n" + "\n".join(missing)
    )


def test_state_rs_wires_triggers_in_both_factories():
    """The desktop app has two ServiceDef factories; each must launch every
    trigger — a single mention could be bundled-only or dev-only."""
    text = STARTUP_FILES["tauri/src-tauri/src/state.rs"].read_text(encoding="utf-8")
    bundled_at = text.find("fn bundled_services")
    dev_at = text.find("fn dev_services")
    assert 0 <= bundled_at < dev_at, "state.rs factory layout changed — update this test"
    sections = {
        "bundled_services": text[bundled_at:dev_at],
        "dev_services": text[dev_at:],
    }
    missing = [
        f"{factory}: {entrypoint}"
        for entrypoint in discover_trigger_entrypoints()
        for factory, body in sections.items()
        if entrypoint not in body
    ]
    assert not missing, "state.rs factories missing triggers:\n" + "\n".join(missing)


def test_run_sh_kills_stale_triggers_on_stop():
    """run.sh stop must pkill each trigger, or restarts leak stale pollers."""
    text = STARTUP_FILES["run.sh"].read_text(encoding="utf-8")
    missing = [
        entrypoint
        for entrypoint in discover_trigger_entrypoints()
        if not re.search(rf'pkill -f "?{entrypoint.rsplit(".", 1)[-1]}"?', text)
    ]
    assert not missing, "run.sh stop path missing pkill for:\n" + "\n".join(missing)
