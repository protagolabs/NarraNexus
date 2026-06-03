#!/usr/bin/env python3
"""
v4 bundle = v3 (4 agents + 7 skills) but:
  - DROP gemini-image-gen
  - ADD netmind-image-gen (Web Developer)  — Qwen/Qwen-Image via /v1/generation
  - ADD netmind-video-gen (Web Developer)  — google/veo3.1-fast via /v1/generation
  - Both pre-seed NETMIND_API_KEY in each skill's .skill_meta.json (Option B)
  - Updated PM + Web Developer awareness to reference netmind skills

⚠️  TWO-STEP DEMO SETUP  ⚠️
The bundle itself is key-free (safe to share). After import, the demo
operator runs `scripts/external_agent_import/seed_netmind_keys.py` once
to populate NETMIND_API_KEY into the freshly-imported Web Developer's
two NetMind skills. This is the temporary B-path while we wait for
Option A (platform-side env injection) or Option C (MCP-tool layer).

Why the seed step is needed: SkillModule.install_skill() overwrites
.skill_meta.json on extract, wiping any env_config shipped inside the
zip. The seed script restores it via set_skill_env_config (which merges).
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
WORK = Path("/tmp/new_bundle_v4")
OUT = Path("/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/scripts/external_agent_import/bundles/web_development_v4.nxbundle")
V2_SKILL_ZIPS = Path("/tmp/web_studio_v2_skills")   # impeccable + frontend-design (carried over)
V4_SKILL_ZIPS = Path("/tmp/v4_skill_zips")          # netmind-image-gen + netmind-video-gen (new)
AWARENESS_DIR = Path("/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/scripts/external_agent_import/web_studio_v2_team/awareness")

WEB_DEV_AGENT_ID = "agent_3bec13b89719"
PM_AGENT_ID = "agent_e1409dbb1318"
VERCEL_AGENT_ID = "agent_dfe93573d888"


# === 1. Fresh extract of coworker's bundle ===
if WORK.exists():
    shutil.rmtree(WORK)
WORK.mkdir(parents=True)
with zipfile.ZipFile(SRC) as z:
    z.extractall(WORK)


# === 2. Copy skill zips into bundle ===
# - netmind-image-gen, netmind-video-gen (new, for Web Developer)
# - impeccable, frontend-design (carried over from v3, for Design Reviewer)
# - DROP gemini-image-gen — replaced by netmind-image-gen
for name in ["netmind-image-gen", "netmind-video-gen"]:
    shutil.copy(V4_SKILL_ZIPS / f"{name}.zip", WORK / "skills" / f"{name}.zip")
for name in ["impeccable", "frontend-design"]:
    shutil.copy(V2_SKILL_ZIPS / f"{name}.zip", WORK / "skills" / f"{name}.zip")


def sha256_of(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# === 3. Mint Design Reviewer agent_id ===
def gen_id(prefix, n=12):
    return f"{prefix}_{''.join(random.choices('0123456789abcdef', k=n))}"


REVIEWER_ID = gen_id("agent", 12)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


OWNER = "<original_owner>"
agent_dir = WORK / "agents" / REVIEWER_ID
agent_dir.mkdir(parents=True)

agent_json = {
    "id": 99,
    "agent_id": REVIEWER_ID,
    "agent_name": "Design Reviewer",
    "created_by": OWNER,
    "agent_description": "Polish/refine the built site using third-party design skills (impeccable + frontend-design). PM dispatches after Vercel deploy.",
    "agent_type": "chat",
    "is_public": 0,
    "agent_metadata": None,
    "agent_create_time": now_iso(),
    "agent_update_time": now_iso(),
}
(agent_dir / "agent.json").write_text(json.dumps(agent_json, indent=2))

REVIEWER_AWARE_INSTANCE_ID = gen_id("aware", 8)
reviewer_awareness_text = (AWARENESS_DIR / "design_reviewer.md").read_text()
(agent_dir / "awareness.json").write_text(json.dumps([{
    "id": 1,
    "instance_id": REVIEWER_AWARE_INSTANCE_ID,
    "awareness": reviewer_awareness_text,
    "created_at": now_iso(),
    "updated_at": now_iso(),
}], indent=2))

buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as tar:
    info = tarfile.TarInfo(name=".keep")
    info.size = 0
    tar.addfile(info, io.BytesIO(b""))
(agent_dir / "workspace.tar.gz").write_bytes(buf.getvalue())


def make_stamp(module_class, instance_id, agent_id, keywords, topic_hint, description):
    return {
        "instance_id": instance_id,
        "module_class": module_class,
        "agent_id": agent_id,
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
    ("AwarenessModule", REVIEWER_AWARE_INSTANCE_ID, ["awareness", "identity", "behavior"], "Agent identity", "Awareness instance for Design Reviewer"),
    ("BasicInfoModule", gen_id("basic", 8), ["info", "metadata"], "Basic agent metadata", "Basic info for Design Reviewer"),
    ("ChatModule", gen_id("chat", 8), ["chat", "conversation", "dialogue"], "Chat interactions and message history", f"Chat instance for user {OWNER}"),
    ("SocialNetworkModule", gen_id("social", 8), ["social", "entities", "graph"], "Social network graph", "Social network instance"),
    ("MessageBusModule", gen_id("bus", 8), ["messagebus", "channels", "inbox"], "MessageBus channels and inbox", "MessageBus instance"),
]
for module_class, inst_id, keywords, topic, desc in stamps_spec:
    mod_dir = agent_dir / "instances" / module_class
    mod_dir.mkdir(parents=True, exist_ok=True)
    (mod_dir / f"{inst_id}.json").write_text(
        json.dumps(make_stamp(module_class, inst_id, REVIEWER_ID, keywords, topic, desc), indent=2)
    )


# === 4. Overlay awareness on PM / Web Developer / Vercel ===
def overlay_awareness(agent_id: str, md_filename: str):
    aware_path = WORK / "agents" / agent_id / "awareness.json"
    existing = json.loads(aware_path.read_text())
    new_text = (AWARENESS_DIR / md_filename).read_text()
    for entry in existing:
        entry["awareness"] = new_text
        entry["updated_at"] = now_iso()
    aware_path.write_text(json.dumps(existing, indent=2))


overlay_awareness(PM_AGENT_ID, "pm.md")
overlay_awareness(WEB_DEV_AGENT_ID, "web_developer.md")
overlay_awareness(VERCEL_AGENT_ID, "vercel.md")


# === 5. Update manifest.json ===
manifest_path = WORK / "manifest.json"
manifest = json.loads(manifest_path.read_text())

manifest["agents"].append(REVIEWER_ID)
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

new_skills = [
    {
        "agent_id": WEB_DEV_AGENT_ID,
        "name": "netmind-image-gen",
        "skill_dir": "netmind-image-gen",
        "install_method": "zip",
        "contains_secrets": False,
        "archive_ref": "skills/netmind-image-gen.zip",
        "sha256": sha256_of(WORK / "skills" / "netmind-image-gen.zip"),
    },
    {
        "agent_id": WEB_DEV_AGENT_ID,
        "name": "netmind-video-gen",
        "skill_dir": "netmind-video-gen",
        "install_method": "zip",
        "contains_secrets": False,
        "archive_ref": "skills/netmind-video-gen.zip",
        "sha256": sha256_of(WORK / "skills" / "netmind-video-gen.zip"),
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
    "v4 TEMP-DEMO: dropped gemini-image-gen, added netmind-image-gen + "
    "netmind-video-gen. Bundle is key-free; demo operator must run "
    "scripts/external_agent_import/seed_netmind_keys.py once after "
    "import to write NETMIND_API_KEY into the skills' env_config. "
    "Image model: Qwen/Qwen-Image (verified). Video: google/veo3.1-fast "
    "(verified accepts jobs)."
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
    "skills_attached": {
        "Web Developer": ["agency-frontend-developer (coworker)", "supabase-postgres-best-practices (coworker)", "supabase (coworker)", "netmind-image-gen (NEW)", "netmind-video-gen (NEW)"],
        "Design Reviewer": ["impeccable (carried v3)", "frontend-design (carried v3)"],
    },
    "key_in_bundle": False,
    "POST_IMPORT_STEP": "Run `python3 scripts/external_agent_import/seed_netmind_keys.py` once after importing this bundle. Reads NETMIND_API_KEY from .env, writes it into Web Developer's netmind-image-gen + netmind-video-gen skill env_configs.",
}, indent=2))
