#!/usr/bin/env python3
"""
Verify Job benchmark Type A — compare actually-created jobs in DB
against expected_jobs in each seed.

Usage:
    .venv/bin/python scripts/verify_jobs_created.py
"""

import json
import sqlite3
from pathlib import Path

DB = Path.home() / ".narranexus" / "nexus.db"
SEEDS_DIR = Path("benchmark/generated_seed_data/job_cases")


def agent_id_from_sample(sample_id: str) -> str:
    import re
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", sample_id)[:60]
    return f"agent_{safe}"


def load_actual_jobs(agent_id: str) -> list[dict]:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT job_id, title, description, job_type, trigger_config, payload, "
        "related_entity_id, monitored_job_ids, status, created_at "
        "FROM instance_jobs WHERE agent_id = ? ORDER BY created_at",
        (agent_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def keyword_hit(haystack: str, needles: list[str]) -> tuple[int, list[str]]:
    haystack_low = (haystack or "").lower()
    matched = [n for n in needles if n.lower() in haystack_low]
    return len(matched), matched


def match_expected_to_actual(expected: dict, actual_jobs: list[dict]) -> dict | None:
    """Find the actual job that best matches an expected_job spec."""
    title_kw = expected.get("title_keywords", [])
    payload_kw = expected.get("payload_keywords", [])
    expected_type = expected.get("job_type")

    best = None
    best_score = 0
    for j in actual_jobs:
        type_ok = (j["job_type"] == expected_type)
        title_n, _ = keyword_hit(j.get("title", ""), title_kw)
        payload_n, _ = keyword_hit(j.get("payload", ""), payload_kw)
        score = (1 if type_ok else 0) * 10 + title_n * 3 + payload_n
        if score > best_score:
            best_score = score
            best = j
    return best if best_score >= 5 else None


def verify_seed(seed_path: Path) -> dict:
    seed = json.loads(seed_path.read_text())
    sample_id = seed["sample_id"]
    agent_id = agent_id_from_sample(sample_id)
    actual_jobs = load_actual_jobs(agent_id)

    expected_range = seed["expected_outcomes"]["expected_job_count_range"]
    expected_jobs = seed["expected_outcomes"]["expected_jobs"]

    matches = []
    matched_actual_ids = set()
    for exp in expected_jobs:
        m = match_expected_to_actual(exp, [j for j in actual_jobs if j["job_id"] not in matched_actual_ids])
        if m is not None:
            matched_actual_ids.add(m["job_id"])

        # Per-field detailed check
        detail = {"label": exp["label"], "matched": m is not None}
        if m:
            tcfg = json.loads(m["trigger_config"]) if m.get("trigger_config") else {}
            trigger_expected = exp.get("trigger_must_contain", {})
            tz_ok = (trigger_expected.get("timezone") is None or
                     tcfg.get("timezone") == trigger_expected.get("timezone"))
            detail.update({
                "actual_title": m["title"],
                "actual_type": m["job_type"],
                "type_match": m["job_type"] == exp.get("job_type"),
                "trigger_actual": tcfg,
                "timezone_match": tz_ok,
                "depends_on": json.loads(m["monitored_job_ids"]) if m.get("monitored_job_ids") else [],
                "depends_on_count_actual": len(json.loads(m["monitored_job_ids"]) or "[]") if m.get("monitored_job_ids") else 0,
                "depends_on_count_expected": exp.get("depends_on_count"),
                "related_entity_actual": m.get("related_entity_id"),
                "related_entity_hint": exp.get("related_entity_hint"),
            })
        matches.append(detail)

    extras = [j for j in actual_jobs if j["job_id"] not in matched_actual_ids]

    return {
        "sample_id": sample_id,
        "expected_count_range": expected_range,
        "actual_count": len(actual_jobs),
        "in_range": expected_range[0] <= len(actual_jobs) <= expected_range[1],
        "matches": matches,
        "extras": [{"title": e["title"], "type": e["job_type"]} for e in extras],
    }


def fmt_report(results: list[dict]) -> str:
    out = []
    overall_pass = 0
    for r in results:
        sid = r["sample_id"]
        cnt = r["actual_count"]
        rng = r["expected_count_range"]
        rng_ok = "✅" if r["in_range"] else "❌"
        out.append(f"\n=== {sid} ===")
        out.append(f"Expected count: {rng}, Actual: {cnt}  {rng_ok}")
        if r["matches"]:
            out.append("Per-job match:")
            for m in r["matches"]:
                if not m["matched"]:
                    out.append(f"  ❌ '{m['label']}' — NOT FOUND in DB")
                    continue
                ind = "✅" if m["type_match"] and m["timezone_match"] else "⚠️"
                out.append(f"  {ind} '{m['label']}' → '{m['actual_title']}'")
                out.append(f"       type={m['actual_type']} (exp={m.get('actual_type','?')}) tz_ok={m['timezone_match']}")
                trig = m["trigger_actual"]
                trig_summary = {k: v for k, v in trig.items() if k in ("cron", "interval_seconds", "run_at", "end_condition", "timezone")}
                out.append(f"       trigger={trig_summary}")
                if m["depends_on_count_expected"] is not None:
                    de_ok = (m["depends_on_count_actual"] == m["depends_on_count_expected"])
                    de_ind = "✅" if de_ok else "❌"
                    out.append(f"       {de_ind} depends_on count: actual={m['depends_on_count_actual']}, expected={m['depends_on_count_expected']}")
                if m["related_entity_hint"] and m["related_entity_hint"] not in ("self", "self_manager"):
                    re_ok = (m["related_entity_hint"] == m["related_entity_actual"])
                    re_ind = "✅" if re_ok else "❌"
                    out.append(f"       {re_ind} related_entity: actual={m['related_entity_actual']}, expected={m['related_entity_hint']}")
        if r["extras"]:
            out.append(f"Extra/unmatched jobs in DB: {len(r['extras'])}")
            for e in r["extras"]:
                out.append(f"  + {e['type']}: {e['title']}")
        if r["in_range"] and all(m["matched"] for m in r["matches"]):
            overall_pass += 1
    out.append(f"\n=== SUMMARY ===")
    out.append(f"Cases with count-in-range AND all expected matched: {overall_pass}/{len(results)}")
    return "\n".join(out)


def main():
    seeds = sorted(SEEDS_DIR.glob("*.json"))
    results = [verify_seed(p) for p in seeds]
    print(fmt_report(results))


if __name__ == "__main__":
    main()
