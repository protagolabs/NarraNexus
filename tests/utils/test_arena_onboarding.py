"""
@file_name: test_arena_onboarding.py
@author: Bin Liang
@date: 2026-06-15
@description: Unit tests for ArenaOnboarder — name generation, the register
              409-retry path (with a fake HTTP client, no network), and the
              workspace file layout that the runtime SkillModule must consume.
"""

import base64
import json
import random
from pathlib import Path

import pytest

from xyz_agent_context.utils.arena_onboarding import (
    ArenaOnboarder,
    ArenaCredentials,
    ArenaApiError,
    GROUP_TEMPERAMENT,
    GROUP_FORCE,
    GROUP_CREATURE,
)
from xyz_agent_context.utils.schema_registry import get_registered_tables


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    """Scripts a sequence of register responses; records the names + URLs attempted."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.attempted_names = []
        self.attempted_urls = []

    def post(self, url, headers=None, json=None, **kw):
        self.attempted_names.append(json["name"])
        self.attempted_urls.append(url)
        return self._responses.pop(0)

    def get(self, url, headers=None, **kw):
        return _FakeResponse(200, {"id": "agent_x", "status": "active"})

    def close(self):
        pass


def _ok(name="Brave_Frost_Fox"):
    return _FakeResponse(201, {
        "agent": {"id": "agent_ABC123", "name": name},
        "credentials": {"api_key": "arena_sk_realkey", "claim_token": "arena_claim_tok"},
    })


def _taken():
    return _FakeResponse(409, {"error": "Agent name already taken", "code": "NAME_TAKEN"})


def test_generate_name_uses_three_groups_and_underscores():
    onb = ArenaOnboarder(rng=random.Random(42))
    name = onb.generate_name()
    parts = name.split("_")
    assert len(parts) == 3
    assert parts[0] in GROUP_TEMPERAMENT
    assert parts[1] in GROUP_FORCE
    assert parts[2] in GROUP_CREATURE
    # alphanumeric + underscore only (Arena's rule)
    assert all(c.isalnum() or c == "_" for c in name)


def test_register_retries_on_409_then_succeeds():
    client = _FakeClient([_taken(), _taken(), _ok()])
    onb = ArenaOnboarder(http_client=client, rng=random.Random(1))
    creds = onb.register()
    assert creds.api_key == "arena_sk_realkey"
    assert creds.agent_id == "agent_ABC123"
    assert creds.claim_token == "arena_claim_tok"
    # claim_url derived from the token
    assert creds.claim_url.endswith("arena_claim_tok")
    assert len(client.attempted_names) == 3  # two 409s then a 201


def test_register_explicit_name_raises_on_409():
    client = _FakeClient([_taken()])
    onb = ArenaOnboarder(http_client=client)
    with pytest.raises(ArenaApiError):
        onb.register(name="Already_Taken_Name")


def test_install_skill_writes_files_consumable_by_skillmodule(tmp_path: Path):
    onb = ArenaOnboarder(http_client=_FakeClient([]))
    creds = ArenaCredentials(
        api_key="arena_sk_xyz",
        agent_id="agent_ARENA",
        agent_name="Swift_Nova_Wolf",
        claim_token="arena_claim_9",
        claim_url="https://arena42.ai/claim/arena_claim_9",
        referral_code="REF-ABC",
    )
    skills_dir = tmp_path / "agent_1_user_1" / "skills"
    result = onb.install_skill(
        skills_dir, creds, skill_md="# Arena\n", owner_user_id="user_1"
    )

    sd = result.skill_dir
    assert sd.name == "arena"
    assert set(result.files_written) == {
        "SKILL.md", ".skill_meta.json", "credentials.json", "arena_profile.json"
    }

    # .skill_meta.json env_config is base64 and decodes to the real values
    meta = json.loads((sd / ".skill_meta.json").read_text())
    env = meta["env_config"]
    assert base64.b64decode(env["ARENA_API_KEY"]).decode() == "arena_sk_xyz"
    assert base64.b64decode(env["ARENA_AGENT_ID"]).decode() == "agent_ARENA"
    assert "ARENA_API_URL" in env and "ARENA_SKILL_VERSION" in env

    # credentials.json carries the claim_token and is chmod 0600
    cj = json.loads((sd / "credentials.json").read_text())
    assert cj["claim_token"] == "arena_claim_9"
    assert (sd / "credentials.json").stat().st_mode & 0o777 == 0o600

    # arena_profile.json is the non-secret record
    pj = json.loads((sd / "arena_profile.json").read_text())
    assert pj["referral_code"] == "REF-ABC"
    assert pj["owner_user_id"] == "user_1"
    assert pj["provisioned_by"] == "NarraNexus"
    # api_key must NOT leak into the non-secret profile
    assert "arena_sk_xyz" not in (sd / "arena_profile.json").read_text()


def test_register_targets_configured_api_base():
    # The configured api_base (e.g. the Arena dev env) is where registration
    # goes — not the hard-coded prod default. This is what keeps dev agents off
    # the live ladder.
    client = _FakeClient([_ok()])
    onb = ArenaOnboarder(
        api_base="https://arena-dev-api.protago-dev.com", http_client=client
    )
    onb.register()
    assert client.attempted_urls == [
        "https://arena-dev-api.protago-dev.com/api/v1/agents/register"
    ]


def test_install_skill_api_url_follows_api_base(tmp_path: Path):
    # The agent's runtime calls read ARENA_API_URL from the installed skill env;
    # it must match the api_base the agent registered against, so a dev agent
    # calls the dev Arena, never prod.
    onb = ArenaOnboarder(
        api_base="https://arena-dev-api.protago-dev.com", http_client=_FakeClient([])
    )
    creds = ArenaCredentials(
        api_key="k", agent_id="a", agent_name="Swift_Nova_Wolf",
        claim_token="t", claim_url="u", referral_code=None,
    )
    result = onb.install_skill(
        tmp_path / "skills", creds, skill_md="# Arena\n", owner_user_id="user_1"
    )
    meta = json.loads((result.skill_dir / ".skill_meta.json").read_text())
    api_url = base64.b64decode(meta["env_config"]["ARENA_API_URL"]).decode()
    assert api_url == "https://arena-dev-api.protago-dev.com"


def test_provisioning_uses_settings_arena_api_base(monkeypatch):
    # The provisioning service must build the onboarder with the configured base
    # (settings.arena_api_base), not ArenaOnboarder's hard-coded default — this
    # is the single wiring line that binds dev provisioning to the dev Arena.
    import asyncio

    from xyz_agent_context.services import arena_provisioning_service as svc

    monkeypatch.setattr(svc.settings, "arena_api_base", "https://arena-dev-api.protago-dev.com")

    captured = {}

    class _CapturingOnboarder:
        def __init__(self, *, api_base, **kw):
            captured["api_base"] = api_base

        def register(self, *a, **kw):
            raise RuntimeError("stop after construction")

        def close(self):
            pass

    monkeypatch.setattr(svc, "ArenaOnboarder", _CapturingOnboarder)

    class _NoAgents:
        def __init__(self, db):
            pass

        async def find(self, *a, **kw):
            return []

    # provision() imports AgentRepository locally from its source module.
    import xyz_agent_context.repository.agent_repository as agent_repo_mod
    monkeypatch.setattr(agent_repo_mod, "AgentRepository", _NoAgents)

    service = svc.ArenaProvisioningService(db_client=object())
    with pytest.raises(RuntimeError, match="stop after construction"):
        asyncio.run(service.provision("user_x"))
    assert captured["api_base"] == "https://arena-dev-api.protago-dev.com"


def test_arena_awareness_has_confidentiality_rule():
    # 铁律 #4 home: the concrete "never leak to competitors" rule lives in the
    # Arena persona, naming the adversarial threat.
    from xyz_agent_context.services.arena_provisioning_service import ARENA_AWARENESS

    text = ARENA_AWARENESS.lower()
    assert "confidentiality" in text
    assert "competitor" in text
    assert "arena_api_key" in text or "credential" in text


def test_generic_awareness_has_confidentiality_rule():
    # Defense-in-depth: the generic confidentiality principle is in the awareness
    # instruction template, so every agent (incl. already-provisioned ones) gets
    # it live, with no scenario naming (铁律 #4: generic stays generic).
    from xyz_agent_context.module.awareness_module.prompts import (
        AWARENESS_MODULE_INSTRUCTIONS,
    )

    text = AWARENESS_MODULE_INSTRUCTIONS.lower()
    assert "confidential" in text
    assert "creator" in text
    # generic layer must not hard-code the Arena scenario
    assert "arena" not in text


def test_no_arena_credentials_table():
    # Arena is an external service: we deliberately keep NO credentials table.
    # The api_key lives only in the agent workspace; idempotency keys on the
    # agents table (agent_metadata.provisioned_source).
    tables = {t.name for t in get_registered_tables()}
    assert "arena_credentials" not in tables
