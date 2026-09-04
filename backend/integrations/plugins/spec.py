"""
@file_name: spec.py
@author: NarraNexus
@date: 2026-08-28
@description: Immutable data contract describing what a coding-agent
              framework plugin is made of and how to tell it apart from
              "not installed".

InstallComponent is the atomic install unit (one pip wheel or one npm
package, always carrying its pinned version in ``requirement`` — the
installer layer never invents a version). PluginSpec groups the components
that together make one user-facing plugin, plus the metadata needed to
report status (probe_package, user_version_source, size_hint).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class InstallComponent:
    """One pip wheel or npm package to install, with its version pinned in.

    ``requirement`` is a ready-to-pass string for the underlying package
    manager (e.g. ``"claude-agent-sdk==0.1.43"`` or
    ``"@anthropic-ai/claude-code@2.1.220"``) — installers never re-derive or
    re-type a version, they only pass this string through.
    """

    kind: Literal["pip", "npm"]
    requirement: str


@dataclass(frozen=True)
class PluginSpec:
    """A user-facing plugin: one or more InstallComponents plus status metadata.

    ``probe_package`` is the python import name that decides "is this
    framework's code present" (mirrors
    ``agent_framework.plugin_paths._FRAMEWORK_PACKAGE``). ``user_version_source``
    picks which single component's detected version is surfaced to the user
    when a plugin has more than one component (Claude Code has both a pip
    wheel and an npm CLI; the CLI's version is the one users recognize).
    """

    id: str
    display_name: str
    framework_name: str
    components: tuple[InstallComponent, ...]
    probe_package: str
    user_version_source: Literal["npm_cli", "pip_pkg"]
    size_hint: str
