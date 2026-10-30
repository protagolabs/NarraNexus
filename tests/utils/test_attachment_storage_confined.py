"""
@file_name: test_attachment_storage_confined.py
@author: Bin Liang
@date: 2026-10-30
@description: ``{workspace}/user_upload_files/<date>/`` is tenant-writable.
``_write_index`` used to write ``_index.json.tmp`` (a FIXED name) with a plain
``open("w")`` there, and the upload itself was ``Path.open("wb")`` after a
``mkdir``: a symlink planted at either name made the backend write attacker-
influenced bytes through it — into another tenant's file, or into any path the
service account can write (a dangling link creates a new file). No race was
needed. Storage, the index and the resolver now run through the link-free walk
(``utils/confined_io``) with random O_EXCL temp names.
"""
from __future__ import annotations

import json
import os

import pytest

from narranexus.platform.settings import settings
from narranexus.platform.utils import attachment_storage as storage
from narranexus.platform.utils.workspace_paths import agent_workspace_relpath

AGENT, USER = "agent_0123456789ab", "u1"
VICTIM_AGENT, VICTIM_USER = "agent_ba9876543210", "u2"
SECRET = b"victim-secret-bytes"


@pytest.fixture
def env(tmp_path, monkeypatch):
    base = tmp_path / "ws"
    monkeypatch.setattr(settings, "base_working_path", str(base), raising=False)
    own = base / agent_workspace_relpath(AGENT, USER)
    vic = base / agent_workspace_relpath(VICTIM_AGENT, VICTIM_USER)
    own.mkdir(parents=True)
    vic.mkdir(parents=True)
    victim_file = vic / "precious.txt"
    victim_file.write_bytes(SECRET)
    return {"base": base, "own": own, "vic": vic, "victim_file": victim_file}


def _date_dir(env):
    return env["own"] / "user_upload_files" / storage._today_str()


def _store(name="a.png", data=b"png-bytes"):
    return storage.store_uploaded_attachment(
        AGENT, USER, raw_bytes=data, original_name=name, mime_type="image/png"
    )


def test_honest_upload_writes_file_and_index_and_resolves(env):
    file_id, path = _store()
    assert path.read_bytes() == b"png-bytes"
    assert path.parent == _date_dir(env).resolve()
    index = json.loads((_date_dir(env) / "_index.json").read_text(encoding="utf-8"))
    assert index[file_id]["filename"] == path.name
    assert storage.resolve_attachment_path(AGENT, USER, file_id) == path
    assert storage.get_attachment_metadata(AGENT, USER, file_id)["size_bytes"] == len(b"png-bytes")
    # no droppings: only the file and the index
    assert sorted(os.listdir(_date_dir(env))) == sorted([path.name, "_index.json"])


def test_two_uploads_the_same_day_both_resolve(env):
    id1, p1 = _store("a.png", b"1")
    id2, p2 = _store("b.png", b"22")
    assert storage.resolve_attachment_path(AGENT, USER, id1) == p1
    assert storage.resolve_attachment_path(AGENT, USER, id2) == p2


def test_a_planted_link_at_the_fixed_tmp_name_is_never_written_through(env):
    """The reproduced bug: _index.json.tmp -> victim file."""
    _date_dir(env).mkdir(parents=True)
    os.symlink(env["victim_file"], _date_dir(env) / "_index.json.tmp")
    file_id, path = _store()
    assert env["victim_file"].read_bytes() == SECRET
    assert storage.resolve_attachment_path(AGENT, USER, file_id) == path


def test_a_planted_dangling_link_at_the_tmp_name_creates_nothing_elsewhere(env, tmp_path):
    _date_dir(env).mkdir(parents=True)
    ghost = tmp_path / "ghost-created-by-backend"
    os.symlink(ghost, _date_dir(env) / "_index.json.tmp")
    _store()
    assert not ghost.exists()


def test_a_planted_link_at_the_index_name_is_replaced_not_written_through(env):
    _date_dir(env).mkdir(parents=True)
    os.symlink(env["victim_file"], _date_dir(env) / "_index.json")
    file_id, path = _store()
    assert env["victim_file"].read_bytes() == SECRET
    assert not os.path.islink(_date_dir(env) / "_index.json")
    assert storage.resolve_attachment_path(AGENT, USER, file_id) == path


def test_a_planted_link_at_the_target_name_is_never_written_through(env, monkeypatch):
    monkeypatch.setattr(storage, "generate_file_id", lambda: "att_deadbeef")
    _date_dir(env).mkdir(parents=True)
    os.symlink(env["victim_file"], _date_dir(env) / "att_deadbeef.png")
    with pytest.raises(FileExistsError):
        _store("a.png")
    assert env["victim_file"].read_bytes() == SECRET


def test_a_swapped_upload_dir_refuses_and_writes_nothing_in_the_victim(env):
    victim_uploads = env["vic"] / "user_upload_files"
    victim_uploads.mkdir()
    os.symlink(victim_uploads, env["own"] / "user_upload_files")
    with pytest.raises(ValueError):
        _store()
    assert os.listdir(victim_uploads) == []


def test_a_swapped_workspace_refuses_and_writes_nothing_in_the_victim(env):
    import shutil

    shutil.rmtree(env["own"])
    os.symlink(env["vic"], env["own"])
    before = sorted(os.listdir(env["vic"]))
    with pytest.raises(ValueError):
        _store()
    assert sorted(os.listdir(env["vic"])) == before


def test_resolver_and_metadata_never_surface_a_swapped_upload_dir(env):
    victim_day = env["vic"] / "user_upload_files" / storage._today_str()
    victim_day.mkdir(parents=True)
    (victim_day / "att_cafe0001.png").write_bytes(SECRET)
    (victim_day / "_index.json").write_text(
        json.dumps({"att_cafe0001": {"filename": "att_cafe0001.png", "mime_type": "image/png"}}),
        encoding="utf-8",
    )
    os.symlink(env["vic"] / "user_upload_files", env["own"] / "user_upload_files")
    assert storage.resolve_attachment_path(AGENT, USER, "att_cafe0001") is None
    assert storage.get_attachment_metadata(AGENT, USER, "att_cafe0001") is None


def test_a_corrupt_or_linked_index_reads_as_empty(env):
    _date_dir(env).mkdir(parents=True)
    (_date_dir(env) / "_index.json").write_text("{not json", encoding="utf-8")
    assert storage._read_index(_date_dir(env)) == {}
    (_date_dir(env) / "_index.json").unlink()
    (env["vic"] / "idx.json").write_text(json.dumps({"x": {}}), encoding="utf-8")
    os.symlink(env["vic"] / "idx.json", _date_dir(env) / "_index.json")
    assert storage._read_index(_date_dir(env)) == {}
