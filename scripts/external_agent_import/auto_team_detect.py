#!/usr/bin/env python3
"""
Auto-team detector — scan all SOUL.md files for cross-agent references and
find cluster candidates.

Strategy:
1. Build a name->id lookup from agents.json (case-insensitive).
2. For each SOUL.md, find references via patterns:
   - **Name** (bolded markdown)
   - `Name` (backticked)
   - Plain "Name (descriptor)" near "Works best with" / "Coordinate with"
   - bare capitalized names that match an agent_id (lower bar)
3. Build a directed graph: agent A -> {agents A mentions}.
4. Score clusters:
   - "Hub-and-spoke": agent A mentions ≥ 2 known agents → team A + mentions
   - "Mutual": A mentions B AND B mentions A → strong team signal
5. Output JSON: list of cluster candidates sorted by quality signal.

Usage:
    python auto_team_detect.py \\
        --repo /tmp/awesome-openclaw-agents \\
        --top 20 \\
        --out detected_teams.json
"""

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path


def load_agents_index(repo: Path) -> tuple[dict, dict]:
    """Return (by_id, by_name_lower)."""
    data = json.loads((repo / "agents.json").read_text(encoding="utf-8"))
    by_id = {a["id"].lower(): a for a in data["agents"]}
    by_name = {}
    for a in data["agents"]:
        # both lower-id and lower-display name as lookup keys
        by_name[a["id"].lower()] = a
        if a.get("name"):
            by_name[a["name"].lower()] = a
        # also strip dashes from id for "Echo" matching when id is "echo"
        by_name[a["id"].replace("-", " ").lower()] = a
    return by_id, by_name


def extract_references(soul_text: str, self_id: str, name_lookup: dict) -> set[str]:
    """Find references to other agents in the SOUL.md.

    Returns a set of self_id-canonicalized references (excluding self).
    """
    found: set[str] = set()

    # Strategy 1: bolded names **Name** or **Name** (anything in **...**)
    for m in re.findall(r"\*\*([A-Z][A-Za-z][\w \-]{0,40})\*\*", soul_text):
        key = m.strip().lower()
        if key in name_lookup:
            tgt = name_lookup[key]["id"].lower()
            if tgt != self_id:
                found.add(tgt)

    # Strategy 2: backticked agent_id-style `name-with-dash`
    for m in re.findall(r"`([a-z][a-z0-9\-]{2,40})`", soul_text):
        key = m.strip().lower()
        if key in name_lookup:
            tgt = name_lookup[key]["id"].lower()
            if tgt != self_id:
                found.add(tgt)

    # Strategy 3: phrases like "Works best with Echo and Radar"
    for trigger in ("works best with", "coordinate with", "delegate to",
                    "hand off to", "integrate with", "alongside"):
        idx = 0
        low = soul_text.lower()
        while True:
            pos = low.find(trigger, idx)
            if pos == -1:
                break
            window = soul_text[pos:pos + 200]
            # Extract capitalized words in the window
            for tok in re.findall(r"\b([A-Z][a-z]{2,})\b", window):
                key = tok.lower()
                if key in name_lookup:
                    tgt = name_lookup[key]["id"].lower()
                    if tgt != self_id:
                        found.add(tgt)
            idx = pos + 1

    return found


def build_graph(repo: Path) -> tuple[dict, dict]:
    """Return (refs_out, by_id) where refs_out[a_id] = {referenced_agent_ids}."""
    by_id, name_lookup = load_agents_index(repo)
    refs_out: dict[str, set[str]] = defaultdict(set)
    for aid, entry in by_id.items():
        soul_path = repo / entry["path"]
        if not soul_path.is_file():
            continue
        try:
            text = soul_path.read_text(encoding="utf-8")
        except Exception:
            continue
        refs_out[aid] = extract_references(text, aid, name_lookup)
    return refs_out, by_id


def score_clusters(refs_out: dict, by_id: dict, top: int) -> list[dict]:
    """Score candidate clusters. A cluster centers on a hub agent that
    mentions ≥ 2 other known agents."""
    clusters = []
    for hub_id, refs in refs_out.items():
        if len(refs) < 2:
            continue
        # Mutual count: how many of the referenced agents also mention the hub
        mutual = sum(1 for tgt in refs if hub_id in refs_out.get(tgt, set()))
        # Cross-category bonus: more cross-category = more interesting team
        hub_cat = by_id[hub_id]["category"]
        ref_cats = {by_id[t]["category"] for t in refs if t in by_id}
        cross_cat = len(ref_cats - {hub_cat})

        score = len(refs) * 2 + mutual * 5 + cross_cat * 3
        members_full = [by_id[hub_id]] + [by_id[t] for t in sorted(refs) if t in by_id]
        clusters.append({
            "hub_id": hub_id,
            "hub_name": by_id[hub_id].get("name") or hub_id,
            "members": [
                {"id": m["id"], "name": m.get("name") or m["id"], "category": m["category"], "path": m["path"]}
                for m in members_full
            ],
            "ref_count": len(refs),
            "mutual_count": mutual,
            "cross_categories": cross_cat,
            "score": score,
        })
    clusters.sort(key=lambda c: c["score"], reverse=True)
    return clusters[:top]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="Path to cloned awesome-openclaw-agents")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    refs_out, by_id = build_graph(Path(args.repo))
    clusters = score_clusters(refs_out, by_id, args.top)

    summary = {
        "total_agents_scanned": len(by_id),
        "agents_with_refs": sum(1 for r in refs_out.values() if r),
        "total_refs": sum(len(r) for r in refs_out.values()),
        "clusters_top_n": args.top,
        "clusters": clusters,
    }
    Path(args.out).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({
        "total_agents_scanned": summary["total_agents_scanned"],
        "agents_with_refs": summary["agents_with_refs"],
        "total_refs": summary["total_refs"],
        "top_clusters_saved_to": str(args.out),
        "top_5_preview": [
            {"hub": c["hub_name"], "size": len(c["members"]), "score": c["score"],
             "categories": list({m["category"] for m in c["members"]}),
             "members": [m["name"] for m in c["members"]]}
            for c in clusters[:5]
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
