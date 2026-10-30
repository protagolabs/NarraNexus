"""
@file_name: test_bus_attachment_confined.py
@author: Bin Liang
@date: 2026-10-30
@description: Bus attachment staging resolved a send-time ref to a REALPATH and
then hard-linked / copied / hashed / stat-ed it by path, and created the shared
area's directories and files by path. The sender controls its own workspace
(links, swapped directories), so a directory swapped between the resolution and
the link made the platform copy ANOTHER tenant's file into the sender's shared
area (a cross-tenant read), and a link planted in the shared area was written
through. Everything now runs through the link-free walk (``utils/confined_io``).

Each ``*_swap_*`` case swaps right AFTER the resolver and asserts the victim's
bytes never reach the staged area and nothing is created in the victim's tree.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from narranexus.platform.message_bus import _bus_attachment_impl as impl
from narranexus.platform.message_bus._bus_attachment_impl import (
    load_bus_attachment_meta,
    resolve_and_stage_refs,
    resolve_shared_file_by_id,
    resolve_shared_file_for_user,
    stage_path_into_team,
    store_bus_attachment_meta,
    store_bytes_into_bus,
)
from narranexus.platform.utils.workspace_paths import agent_workspace_path, bus_files_dir, team_shared_dir

AGENT, OWNER = "agent_sender01", "user_owner01"
V_AGENT, V_OWNER = "agent_victim001", "user_victim01"
SECRET = b"victim-secret-bytes"


def _put(base: Path, agent: str, user: str, rel: str, data: bytes) -> Path:
    p = agent_workspace_path(agent, user, base=str(base)) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _swap_dir_for_link(directory: Path, victim_dir: Path) -> None:
    os.rename(directory, str(directory) + ".moved")
    os.symlink(victim_dir, directory)


def _all_bytes(root: Path) -> list[bytes]:
    out = []
    for dirpath, _d, files in os.walk(root):
        for f in files:
            out.append(Path(dirpath, f).read_bytes())
    return out


@pytest.fixture
def env(tmp_path):
    _put(tmp_path, AGENT, OWNER, "work/a.txt", b"mine")
    _put(tmp_path, V_AGENT, V_OWNER, "secret/a.txt", SECRET)
    return tmp_path


def _swapping_resolver(monkeypatch, env, link_dir: Path, victim_dir: Path):
    real = impl._resolve_ref_to_source
    fired = []

    def wrapper(*a, **k):
        out = real(*a, **k)
        if out is not None and not fired:
            fired.append(True)
            _swap_dir_for_link(link_dir, victim_dir)
        return out

    monkeypatch.setattr(impl, "_resolve_ref_to_source", wrapper)
    return fired


@pytest.mark.asyncio
async def test_stage_swap_after_resolve_never_copies_the_victims_file(env, monkeypatch):
    own_work = agent_workspace_path(AGENT, OWNER, base=str(env)) / "work"
    vic_secret = agent_workspace_path(V_AGENT, V_OWNER, base=str(env)) / "secret"
    fired = _swapping_resolver(monkeypatch, env, own_work, vic_secret)
    staged = await resolve_and_stage_refs(
        sender_agent_id=AGENT, owner_user_id=OWNER, refs=["work/a.txt"], base=str(env)
    )
    assert fired
    assert staged == []
    shared = env / OWNER / "_shared"
    assert SECRET not in (_all_bytes(shared) if shared.exists() else [])


@pytest.mark.asyncio
async def test_team_stage_swap_after_resolve_never_copies_the_victims_file(env, monkeypatch):
    own_work = agent_workspace_path(AGENT, OWNER, base=str(env)) / "work"
    vic_secret = agent_workspace_path(V_AGENT, V_OWNER, base=str(env)) / "secret"
    _swapping_resolver(monkeypatch, env, own_work, vic_secret)
    with pytest.raises(Exception):
        await stage_path_into_team(
            sender_agent_id=AGENT, owner_user_id=OWNER, team_id="team_abc",
            ref="work/a.txt", base=str(env), db=object(),
        )
    shared = env / OWNER / "_shared"
    assert SECRET not in (_all_bytes(shared) if shared.exists() else [])


@pytest.mark.asyncio
async def test_stage_into_a_swapped_shared_dir_creates_nothing_in_the_victim(env):
    (env / V_OWNER / "_shared").mkdir(parents=True)
    (env / OWNER).mkdir(parents=True, exist_ok=True)
    os.symlink(env / V_OWNER / "_shared", env / OWNER / "_shared")
    staged = await resolve_and_stage_refs(
        sender_agent_id=AGENT, owner_user_id=OWNER, refs=["work/a.txt"], base=str(env)
    )
    assert staged == []
    assert list((env / V_OWNER / "_shared").rglob("*")) == []


@pytest.mark.asyncio
async def test_store_bytes_into_a_swapped_bus_dir_creates_nothing_in_the_victim(env):
    victim_bus = bus_files_dir(V_OWNER, str(env))
    victim_bus.mkdir(parents=True)
    (env / OWNER / "_shared").mkdir(parents=True)
    os.symlink(victim_bus, env / OWNER / "_shared" / "bus_files")
    with pytest.raises(ValueError):
        await store_bytes_into_bus(
            user_id=OWNER, raw_bytes=b"payload", original_name="x.txt",
            mime_type="text/plain", base=str(env),
        )
    assert list(victim_bus.rglob("*")) == []


@pytest.mark.asyncio
async def test_a_planted_link_at_the_sidecar_name_is_not_written_through(env):
    att = await store_bytes_into_bus(
        user_id=OWNER, raw_bytes=b"payload", original_name="x.txt", mime_type="text/plain", base=str(env)
    )
    staged = resolve_shared_file_for_user(OWNER, att["rel_path"], str(env))
    sidecar = staged.parent / f"{staged.stem}_meta.json"
    victim_file = env / "victim-file.json"
    victim_file.write_bytes(SECRET)
    os.symlink(victim_file, sidecar)
    store_bus_attachment_meta(OWNER, att, str(env))
    assert victim_file.read_bytes() == SECRET
    assert load_bus_attachment_meta(OWNER, att["rel_path"], str(env))["file_id"] == att["file_id"]


@pytest.mark.asyncio
async def test_shared_resolvers_never_surface_a_swapped_directory(env):
    vday = bus_files_dir(V_OWNER, str(env)) / "2026-10-30"
    vday.mkdir(parents=True)
    (vday / "att_cafe0001.txt").write_bytes(SECRET)
    # the owner's `_shared` is a link to the victim's: ids and rel paths must not resolve
    (env / OWNER).mkdir(parents=True, exist_ok=True)
    os.symlink(env / V_OWNER / "_shared", env / OWNER / "_shared")
    assert resolve_shared_file_by_id(OWNER, "att_cafe0001", str(env)) is None
    assert resolve_shared_file_for_user(OWNER, f"{OWNER}/_shared/bus_files/2026-10-30/att_cafe0001.txt", str(env)) is None


@pytest.mark.asyncio
async def test_honest_stage_store_and_resolve_still_work(env):
    staged = await resolve_and_stage_refs(
        sender_agent_id=AGENT, owner_user_id=OWNER, refs=["work/a.txt"], base=str(env)
    )
    assert len(staged) == 1 and staged[0]["size_bytes"] == 4
    p = resolve_shared_file_for_user(OWNER, staged[0]["rel_path"], str(env))
    assert p is not None and p.read_bytes() == b"mine"
    assert resolve_shared_file_by_id(OWNER, staged[0]["file_id"], str(env)) == p
    uploaded = await store_bytes_into_bus(
        user_id=OWNER, raw_bytes=b"up", original_name="u.txt", mime_type="text/plain", base=str(env)
    )
    assert resolve_shared_file_for_user(OWNER, uploaded["rel_path"], str(env)).read_bytes() == b"up"
    store_bus_attachment_meta(OWNER, uploaded, str(env))
    assert load_bus_attachment_meta(OWNER, uploaded["rel_path"], str(env))["file_id"] == uploaded["file_id"]
    team = await stage_path_into_team(
        sender_agent_id=AGENT, owner_user_id=OWNER, team_id="team_abc", ref="work/a.txt",
        base=str(env), db=_NoDb(),
    )
    assert Path(team["path"]).read_bytes() == b"mine"
    assert Path(team["path"]).parent == team_shared_dir(OWNER, "team_abc", base=str(env))


class _NoDb:
    """The team-file index is best-effort: every repo call may fail."""
    def __getattr__(self, name):
        raise RuntimeError("no db in this test")
