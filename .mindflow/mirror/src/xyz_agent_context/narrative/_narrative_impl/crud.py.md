---
code_file: src/xyz_agent_context/narrative/_narrative_impl/crud.py
last_verified: 2026-08-07
stub: false
---
## 2026-08-07 — `_index_narrative` 不再自己拼文本

改调 `Narrative.searchable_text()`。此前它自己列了同样四个字段但用 `"\n".join`，
而路由侧用 `" ".join`——当前分词器下等价，属于典型的「两份拷贝迟早漂移」。现在是
同一个函数，不是碰巧列了同样字段的第二份实现。详见 [[models.py]]。


# crud.py — Narrative CRUD (+ search-index projection)

## Why it exists

`NarrativeCRUD` is the private create / load / save / query implementation behind `NarrativeService` for the `narratives` operational table. `create()` provisions a new narrative (default user+agent actors and a ChatModule chat instance via `InstanceFactory`, ensuring agent-level instances exist). `save()` / `upsert()` persist it; `load_by_id` / `load_by_agent_user` read it back.

## 2026-06-08 — narrative projected into the unified search index

`save()` and `upsert()` now call `_index_narrative()` after persisting. It projects the narrative's searchable surface (name + current_summary + description + topic_keywords) into `memory_narrative` via `MemoryEngine.index('narrative', id, …)`, with a `source_ref` pointer back to the narrative. This is the SINGLE write point — `create()` flows through `save()` too — so narratives stay findable via `remember` and never go stale. The projected fields are deliberately the SAME ones narrative ROUTING uses (`retrieval.py`), and both share `bm25_rank`, so turn-routing and `remember` rank narratives on identical text. Best-effort: an index failure never breaks narrative persistence.

## Upstream / Downstream

`NarrativeService` → `NarrativeCRUD` → `NarrativeRepository` (operational `narratives` table) + `MemoryEngine` (search projection). `InstanceFactory` for chat-instance provisioning on `create()`.

## Gotchas

- The narrative operational row is the source of truth; `memory_narrative` is a read-only search projection. Deleting a narrative row does NOT currently cascade-delete its index row (known gap — orphan index pointer; tracked in TODO-unified-memory-overhaul.md).
