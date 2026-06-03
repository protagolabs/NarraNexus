#!/usr/bin/env python3
"""
v3 bundle = v2 (4 agents + 7 skills with 3 new third-party skills)
         + rewritten awareness for PM / Web Developer / Design Reviewer / Vercel

Awareness rewrites address the 6 issues from coworker-template testing:
  1. PM guided brief flow (aim / theme / sections / anything-else; never ask tech stack)
  2. PM forbidden from quoting hours; uses register_artifact for live PRD if available
  3. Web Developer confirms with PM only — never DMs user unless user @-mentions
  4. PM auto-dispatches Vercel after Web Developer 'done', updates user same turn
  5. PM auto-dispatches Design Reviewer after Vercel 'live', tells user reviewer
     is also available for ad-hoc 'I'm not sure what to change' asks
  6. PM team roster includes Design Reviewer; Web Developer awareness mentions
     gemini-image-gen + supabase skills
  + Vercel awareness: hand back to PM (not Web Developer) on completion

Input:
  - /Users/ghydsg/Downloads/Web_Development-20260602.nxbundle  (coworker original)
  - /tmp/web_studio_v2_skills/{gemini-image-gen,impeccable,frontend-design}.zip
  - .../web_studio_v2_team/awareness/{pm,web_developer,design_reviewer,vercel}.md
Output:
  - .../bundles/web_development_v3.nxbundle
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
WORK = Path("/tmp/new_bundle_v3")
OUT = Path("/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/scripts/external_agent_import/bundles/web_development_v3.nxbundle")
SKILL_ZIPS = Path("/tmp/web_studio_v2_skills")
AWARENESS_DIR = Path("/Users/ghydsg/Desktop/xyz_proto_test/NarraNexus/scripts/external_agent_import/web_studio_v2_team/awareness")

WEB_DEV_AGENT_ID = "agent_3bec13b89719"  # existing in coworker's bundle
PM_AGENT_ID = "agent_e1409dbb1318"
VERCEL_AGENT_ID = "agent_dfe93573d888"

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

# === 4. Create Design Reviewer agent directory ===
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
    "agent_description": "Polish/refine the built site using third-party design skills (impeccable + frontend-design). PM dispatches after Vercel deploy. Bold, opinionated, concrete refinements.",
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

# Empty workspace.tar.gz
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

# === 5. Overlay rewritten awareness on PM / Web Developer / Vercel ===
def overlay_awareness(agent_id: str, md_filename: str):
    aware_path = WORK / "agents" / agent_id / "awareness.json"
    existing = json.loads(aware_path.read_text())
    new_text = (AWARENESS_DIR / md_filename).read_text()
    # Preserve the existing instance_id and id; only swap awareness text + bump updated_at
    for entry in existing:
        entry["awareness"] = new_text
        entry["updated_at"] = now_iso()
    aware_path.write_text(json.dumps(existing, indent=2))

overlay_awareness(PM_AGENT_ID, "pm.md")
overlay_awareness(WEB_DEV_AGENT_ID, "web_developer.md")
overlay_awareness(VERCEL_AGENT_ID, "vercel.md")

# === 6. Update manifest.json ===
manifest_path = WORK / "manifest.json"
manifest = json.loads(manifest_path.read_text())

# Add Reviewer to agents list + summary
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
    f"v3: rewrote awareness for PM/Web Developer/Vercel + new Design Reviewer awareness. "
    f"Fixes 6 issues from v2 testing: guided brief flow, no time estimates, "
    f"Web-Dev->PM-only comms, auto trigger chain (build->deploy->review), "
    f"PM knows Design Reviewer, Web Developer knows gemini-image-gen."
)

manifest_path.write_text(json.dumps(manifest, indent=2))

# === 7. Re-zip into output ===
OUT.parent.mkdir(parents=True, exist_ok=True)
if OUT.exists():
    OUT.unlink()
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for f in WORK.rglob("*"):
        if f.is_file():
            z.write(f, f.relative_to(WORK))

# === 8. Report ===
print(json.dumps({
    "output_bundle": str(OUT),
    "size_bytes": OUT.stat().st_size,
    "new_agent_id": REVIEWER_ID,
    "awareness_overlays": {
        "pm": {
            "agent_id": PM_AGENT_ID,
            "chars": len((AWARENESS_DIR / "pm.md").read_text()),
        },
        "web_developer": {
            "agent_id": WEB_DEV_AGENT_ID,
            "chars": len((AWARENESS_DIR / "web_developer.md").read_text()),
        },
        "vercel": {
            "agent_id": VERCEL_AGENT_ID,
            "chars": len((AWARENESS_DIR / "vercel.md").read_text()),
        },
        "design_reviewer": {
            "agent_id": REVIEWER_ID,
            "chars": len((AWARENESS_DIR / "design_reviewer.md").read_text()),
        },
    },
    "new_skill_entries": [
        {"agent": "Web Developer", "skill": "gemini-image-gen"},
        {"agent": "Design Reviewer", "skill": "impeccable"},
        {"agent": "Design Reviewer", "skill": "frontend-design"},
    ],
}, indent=2))
