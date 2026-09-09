"""
@file_name: test_helper_strict_schema.py
@date: 2026-09-09
@description: Every helper structured-output model must reach the provider as
an OpenAI-strict-compatible JSON schema.

Dev logs 2026-08-25: 104 x HTTP 400 in one day once the helper slot pointed at
gpt-5.4-mini — ``response_format = json_schema`` with ``strict: true`` was sent
the raw ``model_json_schema()`` of ``ContinuityOutput`` / ``UnifiedMatchOutput``,
which lacks ``additionalProperties: false`` and lists defaulted fields outside
``required``; OpenAI's strict validator rejects both. The ladder then marked
``json_schema`` unsupported and fell to ``json_object`` — the schema level the
ladder exists to prefer was never reachable on a strict provider.

The fix is one seam: ``build_strict_json_schema`` (the same rewrite the
OpenAI SDK applies for ``client.beta.chat.completions.parse``) is what the
json_schema rung sends. These tests pin (a) that rewrite for EVERY model the
codebase passes as ``output_type`` (discovered by scanning the source tree,
so a new helper model is covered without editing this file) and (b) that the
client really sends ``strict: true`` with the rewritten schema — the contract
is only real if both halves hold.
"""
from __future__ import annotations

import importlib
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, Field

from narranexus.platform.agent_framework.adapters import openai_agents as mod
from narranexus.platform.agent_framework.adapters.openai_agents import (
    OpenAIAgentsSDK,
    build_strict_json_schema,
)
from narranexus.platform.narrative._narrative_impl.continuity import ContinuityOutput

_REPO = Path(__file__).resolve().parents[2]
_OUTPUT_TYPE_RE = re.compile(r"output_type=([A-Za-z_][A-Za-z0-9_]*)")


def _module_name(path: Path) -> str | None:
    rel = path.relative_to(_REPO)
    parts = rel.with_suffix("").parts
    if parts[0] == "src":
        return ".".join(parts[1:])
    if parts[0] == "plugins" and len(parts) > 3 and parts[2] == "src":
        return ".".join(parts[3:])
    return None


def _discover_output_models() -> list[tuple[str, type[BaseModel]]]:
    """Every ``output_type=<Name>`` call site under src/ and plugins/*/src/,
    resolved to the Pydantic class in the SAME module (helper models are
    defined next to their one caller)."""
    found: dict[str, type[BaseModel]] = {}
    roots = [_REPO / "src", *sorted((_REPO / "plugins").glob("*/src"))]
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            names = {
                n for n in _OUTPUT_TYPE_RE.findall(text)
                if n not in {"output_type", "None"}
            }
            if not names:
                continue
            module_name = _module_name(path)
            if module_name is None:
                continue
            module = importlib.import_module(module_name)
            for name in sorted(names):
                cls = getattr(module, name, None)
                if isinstance(cls, type) and issubclass(cls, BaseModel):
                    found[f"{module_name}.{name}"] = cls
    return sorted(found.items())


_DISCOVERED = _discover_output_models()


def _object_nodes(node, path=""):
    """Yield (path, node) for every object-shaped node, including $defs."""
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            yield path, node
        for key, value in node.items():
            yield from _object_nodes(value, f"{path}/{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _object_nodes(value, f"{path}[{i}]")


def _strict_violations(schema: dict) -> list[str]:
    out = []
    for path, node in _object_nodes(schema):
        if node.get("additionalProperties") is not False:
            out.append(f"{path or '/'}: additionalProperties must be false")
        props = sorted((node.get("properties") or {}).keys())
        if sorted(node.get("required") or []) != props:
            out.append(f"{path or '/'}: required must list every property")
    return out


# ── (a) every discovered helper model ──────────────────────────────────


def test_discovery_finds_the_helper_models():
    names = {qualified.rsplit(".", 1)[1] for qualified, _ in _DISCOVERED}
    # The two models from the 2026-08-25 incident must be in the sweep.
    assert {"ContinuityOutput", "UnifiedMatchOutput"} <= names
    assert len(_DISCOVERED) >= 16, sorted(names)


@pytest.mark.parametrize(
    "qualified, model", _DISCOVERED, ids=[q for q, _ in _DISCOVERED]
)
def test_every_helper_output_model_builds_a_strict_schema(qualified, model):
    schema = build_strict_json_schema(model)
    assert _strict_violations(schema) == []
    # Field set is preserved — the rewrite tightens, never drops.
    assert set(schema["properties"]) == set(model.model_fields)


def test_raw_pydantic_schema_is_not_strict_for_the_incident_model():
    """Documents why the seam exists: the raw schema is what used to be sent."""
    raw = ContinuityOutput.model_json_schema()
    violations = _strict_violations(raw)
    assert any("additionalProperties" in v for v in violations)
    assert any("required" in v for v in violations)  # confidence/reason default


class _AlreadyStrict(BaseModel):
    model_config = {"extra": "forbid"}
    flag: bool = Field(description="required, no default")


def test_already_strict_model_passes_through_unchanged():
    """Positive / allowed case: a compliant schema is left as is."""
    raw = _AlreadyStrict.model_json_schema()
    assert _strict_violations(raw) == []
    assert build_strict_json_schema(_AlreadyStrict) == raw


# ── (b) the client sends strict=true with the rewritten schema ────────────


def _fake_response(content: str):
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = MagicMock(prompt_tokens=0, completion_tokens=0)
    return resp


@pytest.fixture(autouse=True)
def _clean_capability_cache():
    mod._response_format_capability.clear()
    mod._structured_output_blocklist.clear()
    yield
    mod._response_format_capability.clear()
    mod._structured_output_blocklist.clear()


@pytest.mark.asyncio
async def test_json_schema_rung_sends_strict_true_with_the_strict_schema():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_fake_response(
            '{"is_continuous": true, "confidence": 0.9, "reason": "same topic"}'
        )
    )
    sdk = OpenAIAgentsSDK()
    result = await sdk._fallback_chat_completion(
        client, "gpt-5.4-mini",
        instructions="judge", user_input="q",
        output_type=ContinuityOutput, max_tokens=200,
    )
    assert result.final_output.is_continuous is True

    (call,) = client.chat.completions.create.call_args_list
    rf = call.kwargs["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["name"] == "ContinuityOutput"
    sent = rf["json_schema"]["schema"]
    assert _strict_violations(sent) == [], _strict_violations(sent)
    assert sent == build_strict_json_schema(ContinuityOutput)


def _prompt_schema(client) -> dict:
    system = client.chat.completions.create.call_args.kwargs["messages"][0]
    assert system["role"] == "system"
    return json.loads(system["content"].split("Schema: ", 1)[1])


@pytest.mark.asyncio
async def test_prompt_hint_keeps_the_raw_pydantic_schema():
    """The strict rewrite is for the provider-enforced rung ONLY. The schema
    quoted in the system prompt — what the json_object / prompt-only rungs
    and every non-OpenAI provider read — stays the raw Pydantic schema, so a
    defaulted field (confidence / reason) is still optional there and a weak
    model is not pushed to invent a value for it."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_fake_response('{"is_continuous": true, "reason": "same"}')
    )
    sdk = OpenAIAgentsSDK()
    await sdk._fallback_chat_completion(
        client, "gpt-5.4-mini", instructions="judge", user_input="q",
        output_type=ContinuityOutput, max_tokens=200,
    )
    hinted = _prompt_schema(client)
    assert hinted == ContinuityOutput.model_json_schema()
    assert hinted != build_strict_json_schema(ContinuityOutput)
    assert "confidence" not in hinted.get("required", [])
    # …while the very same call sent the strict rewrite on the rung.
    rf = client.chat.completions.create.call_args.kwargs["response_format"]
    assert rf["json_schema"]["schema"] == build_strict_json_schema(ContinuityOutput)


@pytest.mark.asyncio
async def test_parse_stays_lenient_to_extra_keys_on_lower_rungs():
    """Strictness is enforced provider-side only. The json_object / prompt-only
    rungs have no schema enforcement, and a weaker model that adds a stray key
    must still parse — the rewrite must not leak into client-side validation."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_fake_response(
            '{"is_continuous": false, "confidence": 0.2, "reason": "new", "extra": 1}'
        )
    )
    mod._response_format_capability[mod._capability_key("m")] = {"json_object"}
    sdk = OpenAIAgentsSDK()
    result = await sdk._fallback_chat_completion(
        client, "m", instructions="judge", user_input="q",
        output_type=ContinuityOutput, max_tokens=200,
    )
    assert result.final_output.is_continuous is False
    rf = client.chat.completions.create.call_args.kwargs["response_format"]
    assert rf == {"type": "json_object"}
