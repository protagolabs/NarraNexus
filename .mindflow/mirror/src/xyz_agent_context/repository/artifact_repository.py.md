---
code_file: src/xyz_agent_context/repository/artifact_repository.py
stub: false
last_verified: 2026-05-08
---

# Intent

Pure DB I/O for `instance_artifacts` + `instance_artifact_versions`.
Business rules (quota limits, URL generation, kind validation) live
upstream in a future ArtifactService; this layer is deliberately dumb.

Two tables, one logical entity:
- `instance_artifacts` — the artifact metadata row (owner, session/pinned state, current version)
- `instance_artifact_versions` — append-only content versions (file path + size)

## Upstream

- ArtifactService (not yet written) — will be the only production caller
- Tests (tests/repository/test_artifact_repository.py) — 6 TDD tests using
  real in-memory SQLite via conftest `db_client` fixture

## Downstream

- AsyncDatabaseClient (utils/database.py) — CRUD helpers + `execute` for raw SQL
- schema_registry `instance_artifacts` + `instance_artifact_versions` tables — row shape
- BaseRepository[Artifact] — provides `get_by_id`, `get_by_ids`, `find`, `find_one`

## Design decisions

- `create()` and `delete()` use `self._db.transaction()` context manager to ensure
  the two-table writes are atomic. A partial write (artifact row without version 1,
  or a version row orphaned after a failed artifact delete) would corrupt the quota
  aggregation and leave unreachable file references.

- `iterate()` also runs inside a transaction: it reads `latest_version` (within the
  tx), increments, updates the artifact row, and appends the new version row atomically.
  A read-modify-write outside a transaction would race under concurrent LLM responses
  emitting new artifact versions.

- `set_pinned(pinned=True)` uses a raw SQL UPDATE that explicitly sets `session_id = NULL`
  because `AsyncDatabaseClient.update()` filters out `None` values (to let MySQL DEFAULT
  take effect), so we cannot use the CRUD helper to explicitly nullify a column. Raw SQL
  with `%s` is the correct path for setting NULL in this codebase.

- `list_by_session()` uses raw SQL because the simple `filters` dict passed to
  `BaseRepository.find()` cannot express `AND pinned = 0` alongside
  `session_id = ?` through the high-level API without wrapping in a compound query.

- `total_bytes_for_agent()` joins across both tables to aggregate. Doing it in Python
  (load all versions, sum) would be O(n) round-trips; a single SQL SUM avoids
  the N+1 problem even when an agent has many artifacts and versions.

- `delete()` deletes versions before the artifact row to avoid orphan version rows.
  There is no DB-level FK constraint, so the ordering is enforced here in code.

- Placeholder style is `%s` (MySQL convention). AsyncDatabaseClient translates to `?`
  automatically when the backend dialect is SQLite via `_mysql_to_sqlite_sql`.

## Gotchas

- `_entity_to_row()` uses `1 if entity.pinned else 0` because SQLite stores booleans
  as INTEGER and `bool` in Python would serialize as `True`/`False` (string) if passed
  directly in some paths. Explicit integer coercion is safe across both backends.

- `_row_to_entity()` calls `_parse_bool()` on the `pinned` column because SQLite
  returns INTEGER (0/1), not Python `bool`, from the DB driver.

- `ArtifactVersion.id` is a BIGINT UNSIGNED AUTO_INCREMENT surrogate key. SQLite maps
  this to INTEGER PRIMARY KEY AUTOINCREMENT. The repository reads it back from the row
  after SELECT — it never generates it in Python.

- `list_versions()` intentionally sorts by `version ASC` (not `id ASC`) because
  version is the business sequence number and is guaranteed monotonically increasing
  per artifact. Using `id` would also work but is an implementation detail.

- `COALESCE(SUM(...), 0)` in `total_bytes_for_agent()` is needed because SQL `SUM()`
  over an empty set returns NULL, not 0. Without the COALESCE, the Python `int()` cast
  would raise `TypeError` for a new agent with no artifacts.
