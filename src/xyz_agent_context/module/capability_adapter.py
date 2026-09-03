"""
@file_name: capability_adapter.py
@author: Bin Liang
@date: 2026-09-03
@description: Present an existing ``XYZBaseModule`` as a ``Capability`` (the expand step of the module migration).

Nothing about the module changes: the adapter maps its nine lifecycle methods
onto the stage participations the ``narranexus.contracts.agent`` contract
names, so the turn runtime (and tests) can treat legacy modules and native
capabilities uniformly while the builtin modules are rewritten one by one.
When the last module is native, this file is deleted (the contract step).

Mapping (one-to-one, no semantics change):

    owns_working_source           -> Ingress.claims_source
    hook_data_gathering           -> Assemble.gather
    get_instructions              -> Assemble.contribute_instructions
    get_turn_context              -> Assemble.contribute_turn_context
    get_mcp_config + get_expressive_tools + get_disallowed_tools
                                  -> Assemble.contribute_tools (a ToolSurface)
    hook_persist_turn             -> Commit.persist_turn
    hook_after_event_execution    -> Reflect.after_turn
    get_config                    -> CapabilityMeta
"""
from __future__ import annotations

from typing import Any, Mapping

from narranexus.contracts.agent.capability import CapabilityMeta, CapabilityTier, ToolSurface
from narranexus.contracts.agent.stages import Stage
from xyz_agent_context.module.base import XYZBaseModule


class _IngressParticipant:
    def __init__(self, module: XYZBaseModule) -> None:
        self._module = module

    def claims_source(self, working_source: str) -> bool:
        return self._module.owns_working_source(working_source)


class _AssembleParticipant:
    def __init__(self, module: XYZBaseModule) -> None:
        self._module = module

    async def gather(self, ctx_data: Any) -> Any:
        return await self._module.hook_data_gathering(ctx_data)

    async def contribute_instructions(self, ctx_data: Any) -> str:
        return await self._module.get_instructions(ctx_data)

    async def contribute_turn_context(self, ctx_data: Any) -> str:
        return await self._module.get_turn_context(ctx_data)

    async def contribute_tools(self, ctx_data: Any = None) -> ToolSurface:
        mcp = await self._module.get_mcp_config()
        servers: dict[str, Mapping[str, Any]] = {}
        if mcp is not None:
            servers[mcp.server_name] = mcp.model_dump()
        return ToolSurface(
            mcp_servers=servers,
            expressive_tools=tuple(await self._module.get_expressive_tools(ctx_data)),
            disallowed_tools=tuple(await self._module.get_disallowed_tools(ctx_data)),
        )


class _CommitParticipant:
    def __init__(self, module: XYZBaseModule) -> None:
        self._module = module

    async def persist_turn(self, params: Any) -> None:
        await self._module.hook_persist_turn(params)


class _ReflectParticipant:
    def __init__(self, module: XYZBaseModule) -> None:
        self._module = module

    async def after_turn(self, params: Any) -> None:
        await self._module.hook_after_event_execution(params)


def capability_meta_for(module: XYZBaseModule) -> CapabilityMeta:
    """``CapabilityMeta`` derived from the module's ``ModuleConfig`` and class flags."""
    config = module.get_config()
    return CapabilityMeta(
        name=config.name,
        tier=CapabilityTier.MODULE,
        display_name=config.name,
        description=config.description,
        priority=config.priority,
        always_load=config.module_type == "capability",
        is_task_capability=config.module_type == "task",
        provides_chat_history=type(module).provides_chat_history(),
        requires={"enabled": config.enabled},
    )


class LegacyModuleAdapter:
    """A ``Capability`` view over an ``XYZBaseModule`` instance."""

    def __init__(self, module: XYZBaseModule) -> None:
        self.module = module
        self.meta = capability_meta_for(module)
        self._participations: dict[Stage, Any] = {
            Stage.INGRESS: _IngressParticipant(module),
            Stage.ASSEMBLE: _AssembleParticipant(module),
            Stage.COMMIT: _CommitParticipant(module),
            Stage.REFLECT: _ReflectParticipant(module),
        }

    def participations(self) -> Mapping[Stage, Any]:
        return dict(self._participations)


__all__ = ["LegacyModuleAdapter", "capability_meta_for"]
