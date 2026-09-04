"""
@file_name: scaffold.py
@author: Bin Liang
@date: 2026-09-03
@description: Compose a plugin directory from one or more kind templates (``templates/<kind>/``).

Each template contributes files under ``backend/``, ``frontend/``,
``tests/`` and a fragment of the manifest (``manifest.fragment.json``).
Fragments are deep-merged into one ``narranexus-plugin.json`` (lists
concatenate, dicts merge, scalars last-wins); every file has ``__PLUGIN_ID__``,
``__PLUGIN_PKG__`` (id with dots as underscores), ``__DISPLAY_NAME__`` and
``__TABLE_PREFIX__`` substituted.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from narranexus.contracts.table import table_prefix_for

PLACEHOLDERS = ("__PLUGIN_ID__", "__PLUGIN_PKG__", "__DISPLAY_NAME__", "__TABLE_PREFIX__")
TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "templates"


def _merge(a: Any, b: Any) -> Any:
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = _merge(a[k], v) if k in a else v
        return out
    if isinstance(a, list) and isinstance(b, list):
        return a + [x for x in b if x not in a]
    return b


def substitute(text: str, plugin_id: str, display_name: str) -> str:
    pkg = plugin_id.replace(".", "_").replace("-", "_")
    return (
        text.replace("__PLUGIN_ID__", plugin_id)
        .replace("__PLUGIN_PKG__", pkg)
        .replace("__DISPLAY_NAME__", display_name)
        .replace("__TABLE_PREFIX__", table_prefix_for(plugin_id))
    )


def scaffold(plugin_id: str, kinds: list[str], dest: Path, *, display_name: str, templates_dir: Path = TEMPLATES_DIR) -> list[Path]:
    manifest: dict[str, Any] = {
        "id": plugin_id,
        "version": "0.1.0",
        "displayName": display_name,
        "description": "",
        "license": "MIT",
        "minAppVersion": "0.0.0",
        "hosts": ["backend"],
        "backend": {"package": "backend", "pip": [], "activate": True},
        "quality": "bronze",
    }
    written: list[Path] = []
    init_lines: list[str] = []
    for kind in kinds:
        src = templates_dir / kind
        fragment = src / "manifest.fragment.json"
        if fragment.is_file():
            manifest = _merge(manifest, json.loads(substitute(fragment.read_text(encoding="utf-8"), plugin_id, display_name)))
        for path in sorted(src.rglob("*")):
            if not path.is_file() or path.name == "manifest.fragment.json":
                continue
            rel = path.relative_to(src)
            if rel.parts[0] == "backend" and rel.name == "__init__.py":
                init_lines.append(substitute(path.read_text(encoding="utf-8"), plugin_id, display_name))
                continue
            target = dest / Path(substitute(str(rel), plugin_id, display_name))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(substitute(path.read_text(encoding="utf-8"), plugin_id, display_name), encoding="utf-8")
            written.append(target)
    backend = dest / "backend"
    backend.mkdir(parents=True, exist_ok=True)
    init = backend / "__init__.py"
    header = (
        f'"""{display_name} — a NarraNexus plugin ({plugin_id}).\n\n'
        "Contributions are module-level constants named in narranexus-plugin.json;\n"
        "activate(ctx) runs on the plugin's first activation event.\n"
        '"""\nfrom __future__ import annotations\n\n'
    )
    body = "\n\n".join(init_lines) if init_lines else "def activate(ctx):\n    ctx.log.info('activated')\n"
    init.write_text(header + body + "\n", encoding="utf-8")
    written.append(init)
    mpath = dest / "narranexus-plugin.json"
    mpath.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    written.append(mpath)
    readme = dest / "README.md"
    if not readme.exists():
        readme.write_text(
            f"# {display_name}\n\nA NarraNexus plugin (`{plugin_id}`).\n\n"
            "```bash\nnarranexus plugin link .   # register in place, then restart the app\nuv run pytest tests -q\n"
            "narranexus plugin publish-check .\n```\n",
            encoding="utf-8",
        )
        written.append(readme)
    changelog = dest / "CHANGELOG.md"
    if not changelog.exists():
        changelog.write_text("# Changelog\n\n## 0.1.0\n\n- Initial scaffold.\n", encoding="utf-8")
        written.append(changelog)
    versions = dest / "versions.json"
    if not versions.exists():
        versions.write_text(json.dumps({"0.1.0": manifest["minAppVersion"]}, indent=2) + "\n", encoding="utf-8")
        written.append(versions)
    (dest / "tests").mkdir(exist_ok=True)
    return written


__all__ = ["PLACEHOLDERS", "TEMPLATES_DIR", "scaffold", "substitute"]
