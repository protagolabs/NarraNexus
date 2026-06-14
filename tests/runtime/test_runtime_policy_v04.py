"""
Tests for RuntimePolicy + ExternalAgentRuntime + policy-driven module
behaviour. External API protocol v0.4.

Coverage:
- RuntimePolicy dataclass + EXTERNAL_API_POLICY const sanity
- ExternalAgentRuntime carries policy into self._policy
- ModuleService filters MODULE_MAP by policy.skipped_modules
- ModuleService propagates policy into module constructors
- XYZBaseModule accepts policy kwarg
- GeneralMemoryModule.{_retain_scope, _user_scope_kwargs} branch on policy
- BasicInfoModule._extract_external_session_label parses ephemeral user_id
- step_3 _module_snake produces the expected slugs
- Main runtime (no policy) reproduces today's behaviour
"""
from __future__ import annotations

import pytest

from xyz_agent_context.agent_runtime.agent_runtime import AgentRuntime
from xyz_agent_context.agent_runtime.external_agent_runtime import (
    ExternalAgentRuntime,
    make_external_runtime_factory,
)
from xyz_agent_context.agent_runtime.runtime_policy import (
    DEFAULT_POLICY,
    EXTERNAL_API_POLICY,
    RuntimePolicy,
)
from xyz_agent_context.memory.record import SCOPE_AGENT, SCOPE_USER


# ─── RuntimePolicy dataclass ─────────────────────────────────────────────────


class TestRuntimePolicy:
    def test_default_policy_is_empty(self):
        p = RuntimePolicy()
        assert p.skipped_modules == frozenset()
        assert p.mcp_denylist == frozenset()
        assert p.extra_disallowed_tools == frozenset()
        assert p.hook_denylist == frozenset()
        assert p.awareness_writable is True
        assert p.memory_scope == "agent"
        assert p.identity_block_mode == "owner"

    def test_default_policy_const_matches_empty(self):
        # DEFAULT_POLICY exists as an explicit "no restrictions" sentinel
        assert DEFAULT_POLICY == RuntimePolicy()

    def test_frozen(self):
        p = RuntimePolicy()
        with pytest.raises(Exception):  # FrozenInstanceError
            p.memory_scope = "user"  # type: ignore[misc]

    def test_external_api_policy_fields(self):
        p = EXTERNAL_API_POLICY
        # core fixes for the external session isolation
        assert p.memory_scope == "user"
        assert p.identity_block_mode == "visitor"
        assert p.awareness_writable is False
        # module skipping — agent-wide stateful modules
        assert "SocialNetworkModule" in p.skipped_modules
        assert "LarkModule" in p.skipped_modules
        assert "SlackModule" in p.skipped_modules
        assert "TelegramModule" in p.skipped_modules
        assert "MessageBusModule" in p.skipped_modules
        # MCP suppression — write-style tools the LLM shouldn't see
        assert "AwarenessModule" in p.mcp_denylist
        assert "GeneralMemoryModule" in p.mcp_denylist
        # SDK built-in tools — workspace mutation
        assert "Write" in p.extra_disallowed_tools
        assert "Edit" in p.extra_disallowed_tools
        assert "Bash" in p.extra_disallowed_tools

    def test_skipped_modules_match_actual_MODULE_MAP_keys(self):
        # Catch typos before they silently no-op at filter time
        from xyz_agent_context.module import MODULE_MAP
        for name in EXTERNAL_API_POLICY.skipped_modules:
            assert name in MODULE_MAP, (
                f"EXTERNAL_API_POLICY.skipped_modules has {name!r} "
                f"which is NOT in MODULE_MAP — typo? "
                f"Known modules: {sorted(MODULE_MAP.keys())}"
            )


# ─── ExternalAgentRuntime ────────────────────────────────────────────────────


class TestExternalAgentRuntime:
    def test_carries_policy(self):
        rt = ExternalAgentRuntime(policy=EXTERNAL_API_POLICY)
        assert rt._policy is EXTERNAL_API_POLICY

    def test_main_runtime_has_no_policy(self):
        rt = AgentRuntime()
        assert rt._policy is None  # main runtime ALWAYS None

    def test_factory_returns_external_runtime(self):
        factory = make_external_runtime_factory()
        rt = factory()
        assert isinstance(rt, ExternalAgentRuntime)
        assert rt._policy is EXTERNAL_API_POLICY

    def test_factory_accepts_custom_policy(self):
        custom = RuntimePolicy(memory_scope="user", identity_block_mode="off")
        factory = make_external_runtime_factory(custom)
        rt = factory()
        assert rt._policy is custom

    def test_factory_produces_independent_instances(self):
        factory = make_external_runtime_factory()
        a, b = factory(), factory()
        assert a is not b


# ─── ModuleService filtering ─────────────────────────────────────────────────


class TestModuleServicePolicy:
    def test_skipped_modules_dropped_from_map(self):
        from xyz_agent_context.module import MODULE_MAP, ModuleService

        policy = RuntimePolicy(skipped_modules=frozenset({"SocialNetworkModule"}))
        svc = ModuleService("agt_x", "u_y", None, policy=policy)
        assert "SocialNetworkModule" not in svc._module_map
        # Other modules unaffected
        for name in MODULE_MAP.keys() - {"SocialNetworkModule"}:
            assert name in svc._module_map

    def test_no_policy_reproduces_full_map(self):
        from xyz_agent_context.module import MODULE_MAP, ModuleService

        svc = ModuleService("agt_x", "u_y", None)
        assert set(svc._module_map.keys()) == set(MODULE_MAP.keys())

    def test_policy_propagates_to_loader(self):
        from xyz_agent_context.module import ModuleService

        svc = ModuleService("agt_x", "u_y", None, policy=EXTERNAL_API_POLICY)
        assert svc._loader._policy is EXTERNAL_API_POLICY

    def test_create_module_propagates_policy(self):
        from xyz_agent_context.module import ModuleService

        custom = RuntimePolicy(memory_scope="user")
        svc = ModuleService("agt_x", "u_y", None, policy=custom)
        mod = svc.create_module("GeneralMemoryModule")
        assert mod is not None
        assert mod._policy is custom


# ─── XYZBaseModule policy attribute ──────────────────────────────────────────


class TestModulePolicyAttribute:
    def test_base_module_default_none(self):
        from xyz_agent_context.module.general_memory_module import (
            general_memory_module as gm,
        )

        m = gm.GeneralMemoryModule("agt_x", "u_y", None)
        assert m._policy is None  # main runtime path

    def test_base_module_accepts_policy(self):
        from xyz_agent_context.module.general_memory_module import (
            general_memory_module as gm,
        )

        m = gm.GeneralMemoryModule(
            "agt_x", "u_y", None, policy=EXTERNAL_API_POLICY,
        )
        assert m._policy is EXTERNAL_API_POLICY


# ─── GeneralMemoryModule policy-aware scoping ────────────────────────────────


class TestGeneralMemoryScoping:
    def _make_module(self, *, policy, user_id="ext_xxx_sessA"):
        from xyz_agent_context.module.general_memory_module import (
            general_memory_module as gm,
        )
        return gm.GeneralMemoryModule("agt_x", user_id, None, policy=policy)

    def test_default_retain_scope_is_agent(self):
        m = self._make_module(policy=None)
        scope_type, scope_id = m._retain_scope()
        assert scope_type == SCOPE_AGENT
        assert scope_id == ""

    def test_user_policy_retain_scope_is_user(self):
        m = self._make_module(policy=EXTERNAL_API_POLICY)
        scope_type, scope_id = m._retain_scope()
        assert scope_type == SCOPE_USER
        assert scope_id == "ext_xxx_sessA"

    def test_user_policy_without_user_id_falls_back(self):
        m = self._make_module(policy=EXTERNAL_API_POLICY, user_id=None)
        scope_type, scope_id = m._retain_scope()
        # No user_id → cannot honour SCOPE_USER → fall back to AGENT scope
        assert scope_type == SCOPE_AGENT
        assert scope_id == ""

    def test_default_recall_kwargs_empty(self):
        m = self._make_module(policy=None)
        assert m._user_scope_kwargs() == {}

    def test_user_policy_recall_kwargs_filter(self):
        m = self._make_module(policy=EXTERNAL_API_POLICY)
        kw = m._user_scope_kwargs()
        assert kw == {"scope_type": SCOPE_USER, "scope_id": "ext_xxx_sessA"}

    def test_agent_policy_recall_kwargs_empty(self):
        # explicit memory_scope="agent" (default) should also produce no filter
        explicit_agent = RuntimePolicy(memory_scope="agent")
        m = self._make_module(policy=explicit_agent)
        assert m._user_scope_kwargs() == {}


# ─── BasicInfoModule visitor identity ────────────────────────────────────────


class TestVisitorIdentity:
    def test_label_extracts_session_from_ext_user_id(self):
        from xyz_agent_context.module.basic_info_module import (
            basic_info_module as bi,
        )
        label = bi.BasicInfoModule._extract_external_session_label(
            "ext_a1b2c3d4_visitor_42"
        )
        assert label == "visitor_42"

    def test_label_handles_sanitised_session_with_underscores(self):
        from xyz_agent_context.module.basic_info_module import (
            basic_info_module as bi,
        )
        # The sanitiser collapses anything outside [a-zA-Z0-9_-] to "_"
        # so multi-underscore session_ids are normal.
        label = bi.BasicInfoModule._extract_external_session_label(
            "ext_a1b2c3d4_user__42__abc"
        )
        assert label == "user__42__abc"

    def test_label_unknown_for_blank_input(self):
        from xyz_agent_context.module.basic_info_module import (
            basic_info_module as bi,
        )
        assert bi.BasicInfoModule._extract_external_session_label(None) == "unknown"
        assert bi.BasicInfoModule._extract_external_session_label("") == "unknown"

    def test_label_passthrough_for_non_ext_input(self):
        from xyz_agent_context.module.basic_info_module import (
            basic_info_module as bi,
        )
        # Owner-facing path may pass a non-ext user_id; pass it through as-is.
        result = bi.BasicInfoModule._extract_external_session_label("user_normal_id")
        assert result == "user_normal_id"


# ─── step_3 module name → MCP server-name slug ───────────────────────────────


class TestModuleSnakeCase:
    def test_pascal_to_snake(self):
        from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
            _module_snake,
        )
        assert _module_snake("AwarenessModule") == "awareness_module"
        assert _module_snake("GeneralMemoryModule") == "general_memory_module"
        assert _module_snake("BasicInfoModule") == "basic_info_module"
        assert _module_snake("SocialNetworkModule") == "social_network_module"

    def test_idempotent_on_lowercase(self):
        from xyz_agent_context.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
            _module_snake,
        )
        assert _module_snake("module") == "module"


# ─── Memory coordinator scope kwargs ─────────────────────────────────────────


class TestCoordinatorScopeKwargs:
    @pytest.mark.asyncio
    async def test_remember_forwards_scope_to_engine(self):
        """Verify the new scope_type / scope_id kwargs round-trip into
        engine.recall(). We don't need real DB — we install a stub engine
        and assert it received the right kwargs."""
        from xyz_agent_context.memory.coordinator import MemoryCoordinator

        captured_calls: list[dict] = []

        class _StubEngine:
            agent_id = "agt_x"

            async def recall(self, kind, query, **kwargs):
                captured_calls.append({"kind": kind, "query": query, **kwargs})
                return []

        coord = MemoryCoordinator(_StubEngine())  # type: ignore[arg-type]
        await coord.remember(
            "hello",
            kinds=["observation"],
            scope_type=SCOPE_USER,
            scope_id="ext_xxx_sessA",
        )
        assert captured_calls == [{
            "kind": "observation",
            "query": "hello",
            "limit": 12,
            "scope_type": SCOPE_USER,
            "scope_id": "ext_xxx_sessA",
        }]

    @pytest.mark.asyncio
    async def test_remember_omits_scope_by_default(self):
        """No scope_type/scope_id → passed through as None (engine treats
        None as 'no filter' — backwards compatible)."""
        from xyz_agent_context.memory.coordinator import MemoryCoordinator

        captured_calls: list[dict] = []

        class _StubEngine:
            agent_id = "agt_x"

            async def recall(self, kind, query, **kwargs):
                captured_calls.append({"kind": kind, **kwargs})
                return []

        coord = MemoryCoordinator(_StubEngine())  # type: ignore[arg-type]
        await coord.remember("hi", kinds=["observation"])
        assert captured_calls[0]["scope_type"] is None
        assert captured_calls[0]["scope_id"] is None
