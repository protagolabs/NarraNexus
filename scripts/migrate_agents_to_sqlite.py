"""
Migrate 5 agents (Loki, 顺风耳, 阿良, 千里眼, 巨灵神) from local Docker MySQL
to a local SQLite database.

Usage:
    uv run python scripts/migrate_agents_to_sqlite.py

This script:
1. Connects to Docker MySQL (xyz-mysql) to read old data
2. Creates/opens SQLite DB at ~/.nexusagent/nexusagent.db
3. Auto-migrates schema via schema_registry
4. Copies all relevant tables, changing created_by/user_id from 'user_binliang' to 'binliang'
"""

import asyncio
import json
import sys
from pathlib import Path

# -- Add project to path --
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import aiomysql

MYSQL_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": "xyz_root_pass",
    "db": "xyz_agent_context",
    "charset": "utf8mb4",
}

SQLITE_PATH = Path.home() / ".narranexus" / "nexus.db"

AGENT_IDS = [
    "agent_2b6b9d8ca0cf",   # Loki
    "agent_14cc90056876",    # 顺风耳
    "agent_97b0bde56ba5",    # 阿良
    "agent_b5fb02355f73",    # 千里眼
    "agent_56ce6898efb6",    # 巨灵神
]

# user_id / created_by mapping
USER_MAP = {"user_binliang": "binliang"}


def remap_user(val):
    """Remap old user IDs to new ones."""
    if isinstance(val, str):
        return USER_MAP.get(val, val)
    return val


async def fetch_mysql_rows(pool, query, params=None):
    """Fetch rows from MySQL as list of dicts."""
    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(query, params or ())
            rows = await cur.fetchall()
    return rows


def serialize_value(v):
    """Convert Python objects to SQLite-friendly values."""
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return v


async def insert_rows(sqlite_db, table_name, rows, user_fields=None):
    """Insert rows into SQLite, remapping user fields."""
    if not rows:
        return 0
    user_fields = user_fields or []
    count = 0
    for row in rows:
        data = {}
        for k, v in row.items():
            if k == "id":
                continue  # skip auto-increment
            if k in user_fields:
                v = remap_user(v)
            data[k] = serialize_value(v)
        try:
            await sqlite_db.insert(table_name, data)
            count += 1
        except Exception as e:
            err_str = str(e)
            if "UNIQUE constraint" in err_str or "duplicate" in err_str.lower():
                pass  # skip duplicates
            else:
                print(f"  [WARN] {table_name}: {e}")
    return count


async def main():
    from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend
    from xyz_agent_context.utils.database import AsyncDatabaseClient
    from xyz_agent_context.utils.schema_registry import auto_migrate

    # 1. Connect to MySQL
    print("Connecting to local Docker MySQL...")
    pool = await aiomysql.create_pool(**MYSQL_CONFIG, minsize=1, maxsize=3)

    # 2. Create SQLite DB and auto-migrate schema
    print(f"Creating SQLite database at {SQLITE_PATH}...")
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    backend = SQLiteBackend(str(SQLITE_PATH))
    await backend.initialize()
    sqlite_db = await AsyncDatabaseClient.create_with_backend(backend)

    print("Running schema auto-migration...")
    await auto_migrate(backend)

    # Also ensure the user 'binliang' exists
    existing_user = await sqlite_db.get_one("users", {"user_id": "binliang"})
    if not existing_user:
        print("Creating user 'binliang'...")
        await sqlite_db.insert("users", {
            "user_id": "binliang",
            "role": "admin",
            "user_type": "local",
            "display_name": "Bin Liang",
            "timezone": "Asia/Shanghai",
            "status": "active",
        })

    agent_ids_sql = ",".join(f"'{a}'" for a in AGENT_IDS)

    # 3. Migrate agents
    print("\n=== Migrating agents ===")
    rows = await fetch_mysql_rows(pool, f"SELECT * FROM agents WHERE agent_id IN ({agent_ids_sql})")
    n = await insert_rows(sqlite_db, "agents", rows, user_fields=["created_by"])
    print(f"  agents: {n}/{len(rows)}")

    # 4. Migrate narratives
    print("\n=== Migrating narratives ===")
    rows = await fetch_mysql_rows(pool, f"SELECT * FROM narratives WHERE agent_id IN ({agent_ids_sql})")
    n = await insert_rows(sqlite_db, "narratives", rows)
    print(f"  narratives: {n}/{len(rows)}")

    # 5. Migrate events
    print("\n=== Migrating events ===")
    rows = await fetch_mysql_rows(pool, f"SELECT * FROM events WHERE agent_id IN ({agent_ids_sql})")
    n = await insert_rows(sqlite_db, "events", rows, user_fields=["user_id"])
    print(f"  events: {n}/{len(rows)}")

    # 6. Migrate module_instances
    print("\n=== Migrating module_instances ===")
    rows = await fetch_mysql_rows(pool, f"SELECT * FROM module_instances WHERE agent_id IN ({agent_ids_sql})")
    instance_ids = [r["instance_id"] for r in rows]
    n = await insert_rows(sqlite_db, "module_instances", rows, user_fields=["user_id"])
    print(f"  module_instances: {n}/{len(rows)}")

    if not instance_ids:
        print("No module instances found, skipping instance-related tables.")
        pool.close()
        await pool.wait_closed()
        await sqlite_db.close()
        return

    instance_ids_sql = ",".join(f"'{i}'" for i in instance_ids)

    # 7. Migrate instance_jobs
    print("\n=== Migrating instance_jobs ===")
    rows = await fetch_mysql_rows(pool, f"SELECT * FROM instance_jobs WHERE agent_id IN ({agent_ids_sql})")
    n = await insert_rows(sqlite_db, "instance_jobs", rows, user_fields=["user_id"])
    print(f"  instance_jobs: {n}/{len(rows)}")

    # 8. Migrate instance_awareness
    print("\n=== Migrating instance_awareness ===")
    rows = await fetch_mysql_rows(pool, f"SELECT * FROM instance_awareness WHERE instance_id IN ({instance_ids_sql})")
    n = await insert_rows(sqlite_db, "instance_awareness", rows)
    print(f"  instance_awareness: {n}/{len(rows)}")

    # 9. Migrate instance_narrative_links
    print("\n=== Migrating instance_narrative_links ===")
    rows = await fetch_mysql_rows(pool, f"SELECT * FROM instance_narrative_links WHERE instance_id IN ({instance_ids_sql})")
    n = await insert_rows(sqlite_db, "instance_narrative_links", rows)
    print(f"  instance_narrative_links: {n}/{len(rows)}")

    # 10. Migrate instance_social_entities
    print("\n=== Migrating instance_social_entities ===")
    rows = await fetch_mysql_rows(pool, f"SELECT * FROM instance_social_entities WHERE instance_id IN ({instance_ids_sql})")
    n = await insert_rows(sqlite_db, "instance_social_entities", rows)
    print(f"  instance_social_entities: {n}/{len(rows)}")

    # 11. Migrate instance_json_format_memory_chat
    print("\n=== Migrating instance_json_format_memory_chat ===")
    rows = await fetch_mysql_rows(pool, f"SELECT * FROM instance_json_format_memory_chat WHERE instance_id IN ({instance_ids_sql})")
    n = await insert_rows(sqlite_db, "instance_json_format_memory_chat", rows)
    print(f"  instance_json_format_memory_chat: {n}/{len(rows)}")

    # 12. Migrate module_report_memory (by narrative_id)
    print("\n=== Migrating module_report_memory ===")
    narrative_rows = await fetch_mysql_rows(pool, f"SELECT narrative_id FROM narratives WHERE agent_id IN ({agent_ids_sql})")
    narrative_ids = [r["narrative_id"] for r in narrative_rows]
    if narrative_ids:
        narrative_ids_sql = ",".join(f"'{n}'" for n in narrative_ids)
        rows = await fetch_mysql_rows(pool, f"SELECT * FROM module_report_memory WHERE narrative_id IN ({narrative_ids_sql})")
        n = await insert_rows(sqlite_db, "module_report_memory", rows)
        print(f"  module_report_memory: {n}/{len(rows)}")
    else:
        print("  module_report_memory: 0/0 (no narratives)")

    # 13. Migrate agent_messages
    print("\n=== Migrating agent_messages ===")
    rows = await fetch_mysql_rows(pool, f"SELECT * FROM agent_messages WHERE agent_id IN ({agent_ids_sql})")
    n = await insert_rows(sqlite_db, "agent_messages", rows)
    print(f"  agent_messages: {n}/{len(rows)}")

    # Cleanup
    pool.close()
    await pool.wait_closed()
    await sqlite_db.close()

    print(f"\n=== Migration complete! ===")
    print(f"SQLite database: {SQLITE_PATH}")
    print(f"\nTo use this database, add to .env:")
    print(f'  DATABASE_URL="sqlite:///{SQLITE_PATH}"')


if __name__ == "__main__":
    asyncio.run(main())
