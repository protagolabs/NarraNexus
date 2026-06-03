#!/usr/bin/env python3
"""
Extend the coworker's Web_Development bundle with:
- 1 new skill on Web Developer: gemini-image-gen (real third-party image-gen)
- 1 new agent: Design Reviewer (minimal awareness, behavior driven by skills)
- 2 new skills on Design Reviewer: impeccable + frontend-design
All 3 added as install_method=zip (deterministic, no runtime fetch).
"""
import io
import json
import random
import shutil
import tarfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SRC = Path("/Users/ghydsg/Downloads/Web_Development-20260602.nxbundle")
WORK = Path("/tmp/new_bundle_v2")
OUT = Path("/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/scripts/external_agent_import/bundles/web_development_v2.nxbundle")
SKILL_ZIPS = Path("/tmp/web_studio_v2_skills")

WEB_DEV_AGENT_ID = "agent_3bec13b89719"  # existing (from coworker's bundle)
PM_AGENT_ID = "agent_e1409dbb1318"

# === 1. Fresh extract of coworker's bundle ===
if WORK.exists():
    shutil.rmtree(WORK)
WORK.mkdir(parents=True)
with zipfile.ZipFile(SRC) as z:
    z.extractall(WORK)

# === 2. Copy 3 new skill zips into bundle's skills/ ===
for name in ["gemini-image-gen", "impeccable", "frontend-design"]:
    shutil.copy(SKILL_ZIPS / f"{name}.zip", WORK / "skills" / f"{name}.zip")

def sha256_of(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

# === 3. Mint new Design Reviewer agent_id ===
def gen_id(prefix, n=12):
    return f"{prefix}_{''.join(random.choices('0123456789abcdef', k=n))}"

REVIEWER_ID = gen_id("agent", 12)

# === 4. Create new agent directory ===
def now_iso():
    return datetime.now(timezone.utc).isoformat()

OWNER = "<original_owner>"
agent_dir = WORK / "agents" / REVIEWER_ID
agent_dir.mkdir(parents=True)

# agent.json — minimal, mirrors coworker's pattern
agent_json = {
    "id": 99,
    "agent_id": REVIEWER_ID,
    "agent_name": "Design Reviewer",
    "created_by": OWNER,
    "agent_description": "Refines the built site using third-party design skills (impeccable + frontend-design). Bold aesthetic direction, polish/audit. Skill-driven; awareness deliberately minimal so SKILL.md content dominates.",
    "agent_type": "chat",
    "is_public": 0,
    "agent_metadata": None,
    "agent_create_time": now_iso(),
    "agent_update_time": now_iso(),
}
(agent_dir / "agent.json").write_text(json.dumps(agent_json, indent=2))

# awareness.json — system fallback, like coworker's Web Developer
# (skill SKILL.md will provide actual personality)
AWARE_INSTANCE_ID = gen_id("aware", 8)
awareness_json = [{
    "id": 1,
    "instance_id": AWARE_INSTANCE_ID,
    "awareness": "(You are a helpful assistant. You do not have any special abilities. Please try to ask the user to update your awareness.)",
    "created_at": now_iso(),
    "updated_at": now_iso(),
}]
(agent_dir / "awareness.json").write_text(json.dumps(awareness_json, indent=2))

# Empty workspace.tar.gz
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as tar:
    info = tarfile.TarInfo(name=".keep")
    info.size = 0
    tar.addfile(info, io.BytesIO(b""))
(agent_dir / "workspace.tar.gz").write_bytes(buf.getvalue())

# Instance stamps for 5 default modules
def make_stamp(module_class, instance_id, keywords, topic_hint, description):
    return {
        "instance_id": instance_id,
        "module_class": module_class,
        "agent_id": REVIEWER_ID,
        "user_id": OWNER,
        "is_public": 0,
        "status": "active",
        "description": description,
        "dependencies": "[]",
        "config": "{}",
        "state": None,
        "routing_embedding": None,
        "keywords": json.dumps(keywords),
        "topic_hint": topic_hint,
        "last_used_at": None,
        "completed_at": None,
        "archived_at": None,
        "last_polled_status": None,
        "callback_processed": 0,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

stamps_spec = [
    ("AwarenessModule", AWARE_INSTANCE_ID, ["awareness", "identity", "behavior"], "Agent identity", "Awareness instance for Design Reviewer"),
    ("BasicInfoModule", gen_id("basic", 8), ["info", "metadata"], "Basic agent metadata", "Basic info for Design Reviewer"),
    ("ChatModule", gen_id("chat", 8), ["chat", "conversation", "dialogue"], "Chat interactions and message history", f"Chat instance for user {OWNER}"),
    ("SocialNetworkModule", gen_id("social", 8), ["social", "entities", "graph"], "Social network graph", "Social network instance"),
    ("MessageBusModule", gen_id("bus", 8), ["messagebus", "channels", "inbox"], "MessageBus channels and inbox", "MessageBus instance"),
]
for module_class, inst_id, keywords, topic, desc in stamps_spec:
    mod_dir = agent_dir / "instances" / module_class
    mod_dir.mkdir(parents=True, exist_ok=True)
    (mod_dir / f"{inst_id}.json").write_text(
        json.dumps(make_stamp(module_class, inst_id, keywords, topic, desc), indent=2)
    )

# === 5. Update manifest.json ===
manifest_path = WORK / "manifest.json"
manifest = json.loads(manifest_path.read_text())

# Add to agents list
manifest["agents"].append(REVIEWER_ID)

# Add to agents_summary
ws_size = (agent_dir / "workspace.tar.gz").stat().st_size
manifest["agents_summary"].append({
    "agent_id": REVIEWER_ID,
    "agent_name": "Design Reviewer",
    "narratives": 0,
    "instances": 5,
    "social_entities": 0,
    "rag_rows": 0,
    "artifacts": 0,
    "workspace_size_bytes": ws_size,
    "workspace_path": "workspace.tar.gz",
})

# Add 3 skill entries
new_skills = [
    {
        "agent_id": WEB_DEV_AGENT_ID,
        "name": "gemini-image-gen",
        "skill_dir": "gemini-image-gen",
        "install_method": "zip",
        "contains_secrets": False,
        "archive_ref": "skills/gemini-image-gen.zip",
        "sha256": sha256_of(WORK / "skills" / "gemini-image-gen.zip"),
    },
    {
        "agent_id": REVIEWER_ID,
        "name": "impeccable",
        "skill_dir": "impeccable",
        "install_method": "zip",
        "contains_secrets": False,
        "archive_ref": "skills/impeccable.zip",
        "sha256": sha256_of(WORK / "skills" / "impeccable.zip"),
    },
    {
        "agent_id": REVIEWER_ID,
        "name": "frontend-design",
        "skill_dir": "frontend-design",
        "install_method": "zip",
        "contains_secrets": False,
        "archive_ref": "skills/frontend-design.zip",
        "sha256": sha256_of(WORK / "skills" / "frontend-design.zip"),
    },
]
manifest["skills"].extend(new_skills)
manifest["exported_at"] = now_iso()
manifest.setdefault("info", []).append(
    f"Extended from Web_Development-20260602 — added Design Reviewer agent + 3 third-party skills (gemini-image-gen for Web Developer, impeccable + frontend-design for Design Reviewer)"
)

manifest_path.write_text(json.dumps(manifest, indent=2))

# === 6. Re-zip into output ===
OUT.parent.mkdir(parents=True, exist_ok=True)
if OUT.exists():
    OUT.unlink()
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for f in WORK.rglob("*"):
        if f.is_file():
            z.write(f, f.relative_to(WORK))

# === 7. Report ===
print(json.dumps({
    "output_bundle": str(OUT),
    "size_bytes": OUT.stat().st_size,
    "new_agent_id": REVIEWER_ID,
    "new_skill_entries": [
        {"agent": "Web Developer", "skill": "gemini-image-gen"},
        {"agent": "Design Reviewer", "skill": "impeccable"},
        {"agent": "Design Reviewer", "skill": "frontend-design"},
    ],
    "skill_zip_sizes": {
        "gemini-image-gen": (WORK / "skills" / "gemini-image-gen.zip").stat().st_size,
        "impeccable": (WORK / "skills" / "impeccable.zip").stat().st_size,
        "frontend-design": (WORK / "skills" / "frontend-design.zip").stat().st_size,
    },
}, indent=2))
