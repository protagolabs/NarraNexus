"""
@file_name: store.py
@author:
@date: 2026-08-10
@description: AgentDataStore — the data-access abstraction MCP tools depend on.

Blueprint P0: an MCP tool no longer reaches into repositories/db directly; it
calls this interface. Two implementations, chosen by the composition root
(factory.get_agent_data_store, keyed on NARRANEXUS_BACKEND_URL):

- DirectStore: direct repository access — the CURRENT behavior, byte-for-byte.
  Used locally (`bash run.sh` / DMG) where the process owns the sqlite db.
- HttpStore: calls the backend API, forwarding the caller identity. Used in
  cloud so the mcp container holds NO db credentials (the RCE-remediation goal).

The interface grows one method per migrated tool; awareness's update is first.
Rule #9/#20: tools route through a seam, not a hardcoded call, so Direct↔Http
swaps with no tool change. Rule #21: HttpStore reaches backend over HTTP (never
an import) — the allowed one-way hop.

The backend response contract every Http method must honor
--------------------------------------------------------
The agents routes report *handler* failure as **HTTP 200 with
{"success": false, "error": ...}**, so an Http method that checks only the
status code reports every such failure as success — parse the body. Non-2xx
comes from BEFORE the handler runs, from two sources: (1) transport/middleware,
e.g. the Q6 identity 401; (2) FastAPI's own pydantic argument validation — a
422 when an argument violates a route's Field bounds (query 1-512, limit 1-100,
content <=64KB, source <=512). A method must NEVER let either escape as an
exception: DirectStore only ever returns its own dict/str, so the Http path
degrades every non-2xx to an in-band value the model can read (a 401 means the
deploy set NARRANEXUS_BACKEND_URL before provisioning the identity keys — see
factory.py; a 422 is surfaced as an actionable "invalid arguments" message).

Parity is enforced, not hoped for: the normalizable half of each route's
input bounds is mirrored HERE (`_clamp_limit`, `_remember_reject`,
`_retain_reject`, `_social_id_reject`, `_social_search_reject`,
`_job_query_reject`, `_job_keywords_reject`) and applied by BOTH stores, so a
local caller can never succeed on an input the cloud caller would 422 on. The routes
keep their own pydantic Field bounds regardless — they are a public HTTP
surface reachable without going through HttpStore.

The str-return methods are the deliberate exception to "routes keep Field
bounds": `update_agent_profile` enforces its one input bound (the
AGENT_TEXT_MAX_LENGTH name/description cap) INSIDE the shared
update_agent_profile_from_args that both DirectStore and the route call, and the
route body carries NO Field bound on purpose. A route-level 422 would degrade to
a DIFFERENT string on the HttpStore side ("rejected (422)") than DirectStore's fn
string — there is no `_parse_dict` to fold a 422 back into the tool's shape for a
str return — so the one shared fn is the only place a str-return bound can live
without breaking parity (see profile.py's ProfileUpdateBody note).
"""
from __future__ import annotations

from typing import Optional, Protocol
from urllib.parse import quote

from loguru import logger


def _seg(value: str) -> str:
    """Percent-encode an id used as a URL PATH SEGMENT. LLM-supplied ids
    (narrative_id / event_id / job_id) may contain ``?`` ``#`` ``..`` — without
    encoding those would retarget the request (httpx dot-segment normalization)
    and hit a DIFFERENT resource. Encoding keeps them inside one path segment so
    the route matches its handler and returns DirectStore's same 'not found'.

    ``/`` is the residual case: ``%2F`` is percent-decoded at the ASGI layer, so
    ``.../jobs/a%2Fb`` still route-misses to a 404 (HttpStore degrades to
    'backend rejected (404)') while DirectStore says 'not found: a/b' — a known
    parity seam, but a safe one (404, not a cross-resource retarget). ``safe=""``
    encodes everything including slashes."""
    return quote(str(value), safe="")


class AgentDataStore(Protocol):
    """Data operations an MCP tool needs, transport-agnostic."""

    async def update_awareness(self, agent_id: str, awareness: str) -> str: ...

    async def update_agent_profile(
        self, agent_id: str, new_name: Optional[str], new_description: Optional[str]
    ) -> str: ...

    async def remember(self, agent_id: str, query: str, limit: int) -> dict: ...

    async def grep_memory(
        self, agent_id: str, pattern: str, regex: bool, limit: int
    ) -> dict: ...

    async def memory_retain(self, agent_id: str, content: str, source: str) -> dict: ...

    async def extract_entity_info(
        self, agent_id: str, entity_id: str, updates: dict, update_mode: str
    ) -> dict: ...

    async def merge_entities(
        self, agent_id: str, source_entity_id: str, target_entity_id: str, keep_target_name: bool
    ) -> dict: ...

    async def delete_entity(self, agent_id: str, entity_id: str) -> dict: ...

    async def search_social_network(
        self, agent_id: str, search_keyword: str, search_type: str, top_k: int
    ) -> dict: ...

    async def get_contact_info(self, agent_id: str, entity_id: str) -> dict: ...

    async def get_agent_social_stats(
        self, agent_id: str, sort_by: str, top_k: int, filter_tags: Optional[list]
    ) -> dict: ...

    async def create_agent(
        self, creator_agent_id: str, new_agent_id: str, agent_name: str,
        awareness: str, agent_description: str,
    ) -> dict: ...

    async def view_narrative(self, agent_id: str, narrative_id: str) -> dict: ...

    async def view_event(self, agent_id: str, event_id: str) -> dict: ...

    async def switch_narrative(self, agent_id: str, narrative_id: str) -> dict: ...

    async def job_retrieval_by_id(self, agent_id: str, job_id: str) -> dict: ...

    async def job_retrieval_semantic(
        self, agent_id: str, query: str, user_id: Optional[str], status: Optional[str], limit: int
    ) -> dict: ...

    async def job_retrieval_by_keywords(
        self, agent_id: str, keywords: list, user_id: Optional[str], status: Optional[str], limit: int
    ) -> dict: ...

    async def job_update(self, agent_id: str, job_id: str, fields: dict) -> dict: ...

    async def get_chat_history(self, agent_id: str, instance_id: str, limit: int) -> dict: ...


# Return strings the awareness MCP tool has always produced — DirectStore and
# HttpStore MUST both yield these so migration is behaviour-preserving (parity).
_AWARENESS_OK = "Awareness updated successfully"


def _no_instance_msg(agent_id: str) -> str:
    return f"Error: No AwarenessModule instance found for agent_id={agent_id}"


# The recall/retain input contract, mirrored from general_memory routes'
# pydantic Field bounds. Both stores apply it so they reject the SAME inputs
# (real parity, not happy-path parity). `limit` is normalizable (clamp);
# empty / over-long free text is a genuine rejection (truncating a query or a
# fact would change its meaning), returned as the tool's own failure dict.
_LIMIT_MAX = 100
_QUERY_MAX = 512
_CONTENT_MAX = 65536
_SOURCE_MAX = 512


def _clamp_limit(limit: int) -> int:
    return max(1, min(int(limit), _LIMIT_MAX))


def _remember_reject(query: str) -> Optional[dict]:
    if not query or not query.strip():
        return {"success": False, "error": "query is empty", "memories": []}
    if len(query) > _QUERY_MAX:
        return {"success": False, "error": f"query too long (max {_QUERY_MAX} chars)", "memories": []}
    return None


# grep has its OWN bounds (route Query: pattern 1-256, limit 1-200), different
# from remember's (query 1-512, limit 1-100) — mirror them so Direct and Http
# reject the same inputs. Failure key is `matches` (the grep tool's shape).
_GREP_PATTERN_MAX = 256
_GREP_LIMIT_MAX = 200


def _clamp_grep_limit(limit: int) -> int:
    return max(1, min(int(limit), _GREP_LIMIT_MAX))


def _grep_reject(pattern: str) -> Optional[dict]:
    if not pattern:
        return {"success": False, "error": "pattern is empty", "matches": []}
    if len(pattern) > _GREP_PATTERN_MAX:
        return {"success": False, "error": f"pattern too long (max {_GREP_PATTERN_MAX} chars)", "matches": []}
    return None


def _retain_reject(content: str, source: str) -> Optional[dict]:
    if not content or not content.strip():
        return {"success": False, "error": "content is empty"}
    if len(content) > _CONTENT_MAX:
        return {"success": False, "error": f"content too long (max {_CONTENT_MAX} chars)"}
    if len(source) > _SOURCE_MAX:
        return {"success": False, "error": f"source too long (max {_SOURCE_MAX} chars)"}
    return None


_SOCIAL_ID_MAX = 128


def _social_id_reject(*entity_ids: str) -> Optional[dict]:
    """Mirror the social write routes' entity-id Field bounds so DirectStore and
    HttpStore reject the SAME ids — the route enforces them (ExtractEntityBody /
    MergeEntitiesBody / DeleteEntityBody: Field(min_length=1, max_length=128))
    as a 422, and store.py's parity invariant says both stores must too (else a
    local caller could extract an empty-id entity the cloud caller can't). Uses
    the social tool's ``message`` failure key. Matches the route's Field
    semantics EXACTLY — length-only, NOT strip-based — because the route accepts
    a whitespace id (min_length counts characters); stripping here would itself
    create a Direct/Http divergence."""
    for eid in entity_ids:
        if len(eid) < 1:
            return {"success": False, "message": "entity id is empty"}
        if len(eid) > _SOCIAL_ID_MAX:
            return {"success": False, "message": f"entity id too long (max {_SOCIAL_ID_MAX} chars)"}
    return None


def _social_search_reject(search_keyword: str) -> Optional[dict]:
    """Mirror the /recall route's search_keyword Field(1..512) so both stores
    reject identically. ``results`` key matches the search tool's failure shape."""
    if len(search_keyword) < 1:
        return {"success": False, "message": "search_keyword is empty", "results": []}
    if len(search_keyword) > _QUERY_MAX:
        return {"success": False, "message": f"search_keyword too long (max {_QUERY_MAX} chars)", "results": []}
    return None


def _job_query_reject(query: str) -> Optional[dict]:
    """Mirror the /jobs/search-semantic route's query Field(1..512) so both
    stores reject identically (else empty/over-long query is a local success but
    a cloud 422). Length-only, NOT strip-based — same reasoning as the social
    helpers. Job reads fail with the ``error`` key."""
    if len(query) < 1:
        return {"success": False, "error": "query is empty"}
    if len(query) > _QUERY_MAX:
        return {"success": False, "error": f"query too long (max {_QUERY_MAX} chars)"}
    return None


def _job_keywords_reject(keywords: list) -> Optional[dict]:
    """Mirror the /jobs/search-keywords route's keywords Field(min_length=1)."""
    if not keywords or len(keywords) < 1:
        return {"success": False, "error": "keywords is empty"}
    return None


def _write_message_key(result: dict) -> dict:
    """Fold any ``error``-keyed failure back onto the ``message`` key, so an
    HttpStore dict stays byte-identical to DirectStore (which mirrors the tool,
    and the tools involved fail with ``message``). It is applied on two kinds of
    call site, folding two distinct sources:

    - WRITE methods (social extract/merge/delete, job job_update): the backend
      route's ``_normalize_write_result`` rewrote the tool's ``message`` into
      ``error`` on the wire; this is its exact inverse. Sound because those
      methods fail EXCLUSIVELY with ``message``, so a failure ``error`` on the
      wire always originated as a ``message``.
    - READ methods that route through it too (social search / get_entity_contact
      / get_social_network_stats): their 2xx body is passed through untouched
      (it already uses the tool's keys); here the call only ever bites on
      HttpStore's OWN transport degradations (unreachable / non-2xx), which
      _parse_dict emits as ``error`` — see the inline note at those sites.

    Either way every failure the agent sees is uniform, whichever surface it
    came from."""
    if isinstance(result, dict) and result.get("success") is False and "message" not in result and "error" in result:
        result = dict(result)
        result["message"] = result.pop("error")
    return result


class DirectStore:
    """Local: direct repository access, mirroring the pre-abstraction tool
    bodies. The one deliberate wording change is social's no-instance text: it
    now uses the shared ``social_instance_not_found_msg`` (the route's phrasing)
    instead of the tool's old ``_get_instance_and_module`` string, so Direct and
    Http agree on that edge case — see the social methods below."""

    async def _db(self):
        # The one MCP db entry point (module/base.py) — loop-aware factory
        # semantics documented there; every other MCP tool goes through it.
        from xyz_agent_context.module.base import XYZBaseModule

        return await XYZBaseModule.get_mcp_db_client()

    async def _awareness_instance_id(self, db, agent_id: str) -> Optional[str]:
        from xyz_agent_context.repository import InstanceRepository

        instances = await InstanceRepository(db).get_by_agent(
            agent_id=agent_id, module_class="AwarenessModule"
        )
        return instances[0].instance_id if instances else None

    async def update_awareness(self, agent_id: str, awareness: str) -> str:
        from xyz_agent_context.repository import InstanceAwarenessRepository

        db = await self._db()
        instance_id = await self._awareness_instance_id(db, agent_id)
        if not instance_id:
            return _no_instance_msg(agent_id)
        await InstanceAwarenessRepository(db).upsert(instance_id, awareness)
        return _AWARENESS_OK

    async def update_agent_profile(
        self, agent_id: str, new_name: Optional[str], new_description: Optional[str]
    ) -> str:
        # The whole rename transaction (name/description + identity-note
        # correction + same-owner clash note + discovery refresh) is the shared
        # update_agent_profile_from_args; the backend twin route calls the SAME
        # function, so the two paths return byte-identical strings.
        from xyz_agent_context.module.awareness_module import (
            update_agent_profile_from_args,
        )
        return await update_agent_profile_from_args(
            await self._db(), agent_id,
            new_name=new_name, new_description=new_description,
        )

    async def remember(self, agent_id: str, query: str, limit: int) -> dict:
        # MemoryCoordinator/MemoryEngine go through the repository layer — no
        # raw SQL, dialect-safe on both SQLite and MySQL (unlike chat's
        # information_schema/backtick query, which is why this migrates first).
        # Same input contract as the Http path (parity) — see _remember_reject.
        from xyz_agent_context.memory import MemoryCoordinator, MemoryEngine, format_memory_hits

        reject = _remember_reject(query)
        if reject is not None:
            return reject
        limit = _clamp_limit(limit)
        try:
            db = await self._db()
            coord = MemoryCoordinator(MemoryEngine(db, agent_id))
            hits = await coord.remember(query, limit=limit)
            return {"success": True, "query": query, "memories": format_memory_hits(hits)}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[memory.remember] failed: {e}")
            return {"success": False, "error": str(e), "memories": []}

    async def grep_memory(self, agent_id: str, pattern: str, regex: bool, limit: int) -> dict:
        # Same MemoryCoordinator path as remember (repository layer, dialect-safe);
        # the regex engine is ReDoS-guarded in retrieval.grep_filter, not here.
        # Same input contract as the Http path (parity) — see _grep_reject.
        from xyz_agent_context.memory import MemoryCoordinator, MemoryEngine, format_memory_hits

        reject = _grep_reject(pattern)
        if reject is not None:
            return reject
        limit = _clamp_grep_limit(limit)
        try:
            db = await self._db()
            coord = MemoryCoordinator(MemoryEngine(db, agent_id))
            hits, truncated = await coord.grep_memory(pattern, regex=regex, limit=limit)
            return {"success": True, "pattern": pattern,
                    "matches": format_memory_hits(hits), "truncated": truncated}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[memory.grep_memory] failed: {e}")
            return {"success": False, "error": str(e), "matches": []}

    async def memory_retain(self, agent_id: str, content: str, source: str) -> dict:
        from xyz_agent_context.memory import MemoryEngine, MemoryRecord, SCOPE_AGENT

        reject = _retain_reject(content, source)
        if reject is not None:
            return reject
        try:
            db = await self._db()
            engine = MemoryEngine(db, agent_id)
            tags = ["imported"] if source else []
            rec = await engine.retain(MemoryRecord(
                agent_id=agent_id, scope_type=SCOPE_AGENT, kind="observation",
                subtype="world", content_text=content.strip(),
                tags=tags, proof_count=1,
                source_ref={"kind": "import", "id": source} if source else None,
            ))
            return {"success": True, "record_id": rec.record_id}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[memory.memory_retain] failed: {e}")
            return {"success": False, "error": str(e)}

    async def _social_module(self, agent_id: str):
        """Resolve the agent's SocialNetworkModule instance and build a temp
        module bound to it — the same (instance lookup + module construction)
        the backend social routes do (this is where the tool's old
        ``_get_instance_and_module`` logic moved).

        Returns (module, instance_id, None) on success, or (None, None,
        failure_dict) where failure_dict is the seam's own ``message``-shaped
        dict — for a missing instance OR any db/resolution error — so a caller
        never sees an exception escape (the DirectStore invariant, module
        docstring: only ever return a dict; the memory methods keep it the same
        way). SocialNetworkModule is imported lazily here to avoid a circular
        import at module load."""
        from xyz_agent_context.repository import InstanceRepository
        from xyz_agent_context.module.social_network_module import (
            SocialNetworkModule,
            social_instance_not_found_msg,
        )

        try:
            db = await self._db()
            instances = await InstanceRepository(db).get_by_agent(
                agent_id=agent_id, module_class="SocialNetworkModule"
            )
            if not instances:
                return None, None, {"success": False, "message": social_instance_not_found_msg(agent_id)}
            instance_id = instances[0].instance_id
            module = SocialNetworkModule(agent_id=agent_id, database_client=db, instance_id=instance_id)
            return module, instance_id, None
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[social] instance resolution failed for {agent_id}: {e}")
            return None, None, {"success": False, "message": f"Error: {e}"}

    async def extract_entity_info(
        self, agent_id: str, entity_id: str, updates: dict, update_mode: str
    ) -> dict:
        # Mirrors the extract_entity_info tool's post-parse body: resolve the
        # instance, then delegate to the module's pure-repository merge. Every
        # exit is an in-band ``message``-shaped dict — the module method catches
        # its own errors, and _social_module + this try/except catch resolution
        # / call failures, so DirectStore never raises (parity with HttpStore).
        reject = _social_id_reject(entity_id)
        if reject is not None:
            return reject
        module, instance_id, err = await self._social_module(agent_id)
        if err is not None:
            return err
        try:
            return await module.extract_and_update_entity_info(
                entity_id=entity_id, instance_id=instance_id, updates=updates, update_mode=update_mode
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[social.extract_entity_info] failed: {e}")
            return {"success": False, "message": f"Error: {e}"}

    async def merge_entities(
        self, agent_id: str, source_entity_id: str, target_entity_id: str, keep_target_name: bool
    ) -> dict:
        reject = _social_id_reject(source_entity_id, target_entity_id)
        if reject is not None:
            return reject
        module, instance_id, err = await self._social_module(agent_id)
        if err is not None:
            return err
        try:
            return await module.merge_entities(
                source_entity_id=source_entity_id, target_entity_id=target_entity_id,
                instance_id=instance_id, keep_target_name=keep_target_name,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[social.merge_entities] failed: {e}")
            return {"success": False, "message": f"Error: {e}"}

    async def delete_entity(self, agent_id: str, entity_id: str) -> dict:
        reject = _social_id_reject(entity_id)
        if reject is not None:
            return reject
        module, instance_id, err = await self._social_module(agent_id)
        if err is not None:
            return err
        try:
            return await module.delete_entity(entity_id=entity_id, instance_id=instance_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[social.delete_entity] failed: {e}")
            return {"success": False, "message": f"Error: {e}"}

    async def search_social_network(
        self, agent_id: str, search_keyword: str, search_type: str, top_k: int
    ) -> dict:
        reject = _social_search_reject(search_keyword)
        if reject is not None:
            return reject
        top_k = _clamp_limit(top_k)
        module, instance_id, err = await self._social_module(agent_id)
        if err is not None:
            return {**err, "results": []}  # search tool's no-instance shape
        try:
            return await module.search_network(
                search_keyword=search_keyword, instance_id=instance_id,
                search_type=search_type, top_k=top_k,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[social.search_social_network] failed: {e}")
            return {"success": False, "message": f"Error: {e}", "results": []}

    async def get_contact_info(self, agent_id: str, entity_id: str) -> dict:
        from xyz_agent_context.module.social_network_module import format_contact_result

        reject = _social_id_reject(entity_id)
        if reject is not None:
            return reject
        module, instance_id, err = await self._social_module(agent_id)
        if err is not None:
            return err  # get_contact_info's no-instance shape (no results key)
        try:
            recall = await module.recall_entity_info(entity_id, instance_id)
            return format_contact_result(entity_id, recall)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[social.get_contact_info] failed: {e}")
            return {"success": False, "message": f"Error: {e}"}

    async def get_agent_social_stats(
        self, agent_id: str, sort_by: str, top_k: int, filter_tags: Optional[list]
    ) -> dict:
        from xyz_agent_context.module.social_network_module import format_stats_result

        top_k = _clamp_limit(top_k)
        module, instance_id, err = await self._social_module(agent_id)
        if err is not None:
            return {**err, "results": []}  # stats tool's no-instance shape
        try:
            stats = await module.get_agent_stats(
                instance_id=instance_id, sort_by=sort_by, top_k=top_k, filter_tags=filter_tags,
            )
            return format_stats_result(sort_by, stats)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[social.get_agent_social_stats] failed: {e}")
            return {"success": False, "message": f"Error: {e}", "results": []}

    async def create_agent(
        self, creator_agent_id: str, new_agent_id: str, agent_name: str,
        awareness: str, agent_description: str,
    ) -> dict:
        # Resolve the creator's owner, provision the new agent under that owner
        # with the caller-minted new_agent_id, and shape the result via the
        # shared format_create_agent_success — same path the create-agent route
        # takes, so Direct and Http return byte-identical output. Message-shaped
        # failures; never raises (DirectStore invariant).
        from xyz_agent_context.repository import AgentRepository
        from xyz_agent_context.bootstrap.provision import provision_new_agent
        from xyz_agent_context.module.social_network_module import (
            format_create_agent_success,
            CREATE_AGENT_NO_OWNER_MSG,
        )

        try:
            db = await self._db()
            caller = await AgentRepository(db).get_agent(creator_agent_id)
            if not caller or not caller.created_by:
                return {"success": False, "message": CREATE_AGENT_NO_OWNER_MSG}
            result = await provision_new_agent(
                db,
                agent_id=new_agent_id,
                user_id=caller.created_by,
                agent_name=agent_name,
                agent_description=agent_description or f"Agent created by {caller.agent_name or creator_agent_id}",
                awareness=awareness,
            )
            # Match the route's create log so local-mode 'who created which agent
            # when' is not silent (the route logs this too).
            logger.info(f"Created agent {new_agent_id} ('{agent_name}') for owner {caller.created_by}")
            return format_create_agent_success(agent_name, new_agent_id, result.warnings)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[social.create_agent] failed: {e}")
            return {"success": False, "message": f"Error: {e}"}

    # basic_info narrative/event reads. The fetch_*/check_* helpers are
    # dialect-safe (get_one/get/get_by_ids, no raw SQL) and self-contained (they
    # return a dict, never raise), and the narrative routes call the SAME helpers
    # — so Direct and Http are byte-identical. The outer try only guards the
    # _db() acquisition so DirectStore still never raises.
    async def view_narrative(self, agent_id: str, narrative_id: str) -> dict:
        from xyz_agent_context.module.basic_info_module import fetch_narrative_view

        try:
            result = await fetch_narrative_view(await self._db(), agent_id, narrative_id)
            logger.info(f"[basic_info.view_narrative] {narrative_id} -> {result.get('message_count')} messages")
            return result
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[basic_info.view_narrative] failed: {e}")
            return {"success": False, "error": str(e)}

    async def view_event(self, agent_id: str, event_id: str) -> dict:
        from xyz_agent_context.module.basic_info_module import fetch_event_view

        try:
            result = await fetch_event_view(await self._db(), agent_id, event_id)
            logger.info(f"[basic_info.view_event] {event_id} -> success={result.get('success')}")
            return result
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[basic_info.view_event] failed: {e}")
            return {"success": False, "error": str(e)}

    async def switch_narrative(self, agent_id: str, narrative_id: str) -> dict:
        from xyz_agent_context.module.basic_info_module import check_narrative_switch

        try:
            result = await check_narrative_switch(await self._db(), agent_id, narrative_id)
            logger.info(f"[basic_info.switch_narrative] {narrative_id} -> success={result.get('success')}")
            return result
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[basic_info.switch_narrative] failed: {e}")
            return {"success": False, "error": str(e)}

    # Job reads. The fetch/search helpers are dialect-safe (JobRepository) and
    # self-contained (return a dict, never raise); the job routes call the SAME
    # helpers, so Direct and Http are byte-identical. limit is clamped in both
    # stores to the route's le=100 bound (parity). The outer try only guards
    # _db() so DirectStore still never raises.
    async def job_retrieval_by_id(self, agent_id: str, job_id: str) -> dict:
        from xyz_agent_context.module.job_module import fetch_job_by_id

        try:
            return await fetch_job_by_id(await self._db(), agent_id, job_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[job.job_retrieval_by_id] failed: {e}")
            return {"success": False, "error": str(e)}

    async def job_retrieval_semantic(
        self, agent_id: str, query: str, user_id: Optional[str], status: Optional[str], limit: int
    ) -> dict:
        from xyz_agent_context.module.job_module import search_jobs_semantic

        reject = _job_query_reject(query)
        if reject is not None:
            return reject
        try:
            return await search_jobs_semantic(
                await self._db(), agent_id, query, user_id, status, _clamp_limit(limit),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[job.job_retrieval_semantic] failed: {e}")
            return {"success": False, "error": str(e)}

    async def job_retrieval_by_keywords(
        self, agent_id: str, keywords: list, user_id: Optional[str], status: Optional[str], limit: int
    ) -> dict:
        from xyz_agent_context.module.job_module import search_jobs_by_keywords

        reject = _job_keywords_reject(keywords)
        if reject is not None:
            return reject
        try:
            return await search_jobs_by_keywords(
                await self._db(), agent_id, keywords, user_id, status, _clamp_limit(limit),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[job.job_retrieval_by_keywords] failed: {e}")
            return {"success": False, "error": str(e)}

    async def job_update(self, agent_id: str, job_id: str, fields: dict) -> dict:
        # The shared update_job_from_args is self-contained (returns a
        # message-keyed dict, never raises) — the backend job routes call the
        # SAME function, so Direct and Http are byte-identical. The outer try
        # only guards _db().
        from xyz_agent_context.module.job_module import update_job_from_args

        try:
            return await update_job_from_args(await self._db(), agent_id, job_id, **fields)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[job.job_update] failed: {e}")
            return {"success": False, "job_id": job_id, "message": f"Error: {e}"}

    async def get_chat_history(self, agent_id: str, instance_id: str, limit: int) -> dict:
        # fetch_chat_history is self-contained (instance-scoped, de-rawed, returns
        # the tool's dict, never raises) — the backend twin route calls the SAME
        # function, so Direct and Http are byte-identical. The outer try only
        # guards _db() (lazy MySQL pool build can raise) so DirectStore keeps the
        # "never raises, only returns a dict" invariant — the twin route wraps
        # get_db_client() for the same reason.
        from xyz_agent_context.module.chat_module import fetch_chat_history

        try:
            return await fetch_chat_history(await self._db(), agent_id, instance_id, limit)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[chat.get_chat_history] failed: {e}")
            return {"success": False, "instance_id": instance_id, "error": str(e),
                    "total_messages": 0, "messages": []}


class HttpStore:
    """Cloud: call the backend API (no db creds in mcp).

    Forwards the caller identity so the backend can authenticate the call
    (blueprint Q6 — the nx-agent bearer's identity_token is verified there,
    and each route runs its own OWNER check). ``identity_headers`` is the same
    header set the executor→mcp hop already carries; see
    factory.get_agent_data_store for wiring.

    Every method goes through `_send` (one transport + HTTPError boundary),
    pre-applies the shared input contract (`_clamp_limit`/`_*_reject`) so it
    rejects the same inputs DirectStore does, sends ``create_missing=false``-
    style parity switches where a route's convenience semantics (auto-create)
    would diverge from the direct path, and parses the 200+success:false /
    non-2xx failure shapes via `_parse_dict` — see the module docstring.
    """

    def __init__(self, backend_url: str, identity_headers: Optional[dict] = None) -> None:
        self._base = backend_url.rstrip("/")
        self._headers = identity_headers or {}

    async def update_awareness(self, agent_id: str, awareness: str) -> str:
        r = await self._send(
            "PUT",
            f"/api/agents/{agent_id}/awareness",
            params={"create_missing": "false"},
            json={"awareness": awareness},
        )
        if r is None:
            return "Error: awareness backend unreachable"
        if r.status_code >= 400:
            # Transport/middleware-layer rejection (the route's handler always
            # answers 200) — most likely the Q6 identity gate. In-band, never
            # an exception: the direct path only ever returns strings.
            logger.warning(
                f"[data-access] awareness backend rejected the call: {r.status_code}"
            )
            return f"Error: awareness backend rejected the call ({r.status_code})"
        try:
            body = r.json() or {}
        except ValueError:
            return "Error: awareness backend returned a non-JSON response"
        if not body.get("success"):
            error = str(body.get("error") or "unknown backend error")
            return error if error.startswith("Error:") else f"Error: {error}"
        return _AWARENESS_OK

    async def update_agent_profile(
        self, agent_id: str, new_name: Optional[str], new_description: Optional[str]
    ) -> str:
        # update_agent_profile returns a DYNAMIC status string (which fields
        # changed + any same-owner clash note), not a fixed constant like
        # awareness — so the route hands back {"message": <the exact tool
        # string>} and we return it verbatim (parity with DirectStore, which
        # returns the shared fn's string). Transport failures degrade to an
        # "Error: ..." string; the direct path only ever returns strings, so we
        # must never surface a dict or raise.
        r = await self._send(
            "POST",
            f"/api/agents/{agent_id}/profile/update",
            json={"new_name": new_name, "new_description": new_description},
        )
        if r is None:
            return "Error: profile backend unreachable"
        if r.status_code >= 400:
            logger.warning(
                f"[data-access] profile backend rejected the call: {r.status_code}"
            )
            return f"Error: profile backend rejected the call ({r.status_code})"
        try:
            body = r.json() or {}
        except ValueError:
            return "Error: profile backend returned a non-JSON response"
        message = body.get("message")
        if not isinstance(message, str):
            return "Error: profile backend returned no message"
        return message

    async def remember(self, agent_id: str, query: str, limit: int) -> dict:
        # The backend route returns the EXACT dict shape the tool produces
        # (shared format_memory_hits, same {success, query, memories} keys), so
        # on 2xx the body is returned verbatim. Pre-apply the shared input
        # contract so Direct and Http reject identically and the common
        # limit>100 overreach never becomes a hard 422 (parity). Transport
        # failures degrade to the tool's own shape — never an exception.
        reject = _remember_reject(query)
        if reject is not None:
            return reject
        limit = _clamp_limit(limit)
        return await self._get_dict(
            f"/api/agents/{agent_id}/memory/remember",
            params={"query": query, "limit": limit},
            failure_extra={"memories": []},
        )

    async def grep_memory(self, agent_id: str, pattern: str, regex: bool, limit: int) -> dict:
        # The route returns the EXACT {success, pattern, matches} dict the tool
        # produces (shared format_memory_hits), so a 2xx body is returned verbatim.
        # Pre-apply grep's OWN input contract (pattern 1-256, limit clamp 200 —
        # NOT remember's) so Direct and Http reject identically. Transport failures
        # degrade to the tool's shape. `regex` is serialized lowercase so FastAPI's
        # bool parser accepts it.
        reject = _grep_reject(pattern)
        if reject is not None:
            return reject
        limit = _clamp_grep_limit(limit)
        return await self._get_dict(
            f"/api/agents/{agent_id}/memory/grep",
            params={"pattern": pattern, "regex": str(bool(regex)).lower(), "limit": limit},
            failure_extra={"matches": []},
        )

    async def memory_retain(self, agent_id: str, content: str, source: str) -> dict:
        reject = _retain_reject(content, source)
        if reject is not None:
            return reject
        return await self._post_dict(
            f"/api/agents/{agent_id}/memory/retain",
            json={"content": content, "source": source},
            failure_extra={},
        )

    # Social writes: the backend routes (PR-2) are byte-parity twins that
    # delegate to the SAME SocialNetworkModule methods DirectStore calls, so a
    # 2xx body already matches. The one gap is the failure key: the route runs
    # ``_normalize_write_result`` (message->error) for its HTTP family, so every
    # social response is passed back through ``_write_message_key`` to
    # restore the tool's ``message`` shape — see that helper.
    async def extract_entity_info(
        self, agent_id: str, entity_id: str, updates: dict, update_mode: str
    ) -> dict:
        reject = _social_id_reject(entity_id)
        if reject is not None:
            return reject
        return _write_message_key(await self._post_dict(
            f"/api/agents/{agent_id}/social-network/extract",
            json={"entity_id": entity_id, "updates": updates, "update_mode": update_mode},
            failure_extra={},
        ))

    async def merge_entities(
        self, agent_id: str, source_entity_id: str, target_entity_id: str, keep_target_name: bool
    ) -> dict:
        reject = _social_id_reject(source_entity_id, target_entity_id)
        if reject is not None:
            return reject
        return _write_message_key(await self._post_dict(
            f"/api/agents/{agent_id}/social-network/merge",
            json={
                "source_entity_id": source_entity_id,
                "target_entity_id": target_entity_id,
                "keep_target_name": keep_target_name,
            },
            failure_extra={},
        ))

    async def delete_entity(self, agent_id: str, entity_id: str) -> dict:
        reject = _social_id_reject(entity_id)
        if reject is not None:
            return reject
        return _write_message_key(await self._post_dict(
            f"/api/agents/{agent_id}/social-network/delete-entity",
            json={"entity_id": entity_id},
            failure_extra={},
        ))

    # Social reads: the /recall, /contact, /stats twin routes return the tool
    # dict shape verbatim (message-keyed), so on 2xx the body passes straight
    # through. _write_message_key only bites on _parse_dict's own transport
    # degradations (which are error-keyed) — mapping them onto the tool's
    # `message` key so every social failure the agent sees is uniform.
    async def search_social_network(
        self, agent_id: str, search_keyword: str, search_type: str, top_k: int
    ) -> dict:
        reject = _social_search_reject(search_keyword)
        if reject is not None:
            return reject
        top_k = _clamp_limit(top_k)
        return _write_message_key(await self._post_dict(
            f"/api/agents/{agent_id}/social-network/recall",
            json={"search_keyword": search_keyword, "search_type": search_type, "top_k": top_k},
            failure_extra={"results": []},
        ))

    async def get_contact_info(self, agent_id: str, entity_id: str) -> dict:
        reject = _social_id_reject(entity_id)
        if reject is not None:
            return reject
        return _write_message_key(await self._post_dict(
            f"/api/agents/{agent_id}/social-network/contact",
            json={"entity_id": entity_id},
            failure_extra={},
        ))

    async def get_agent_social_stats(
        self, agent_id: str, sort_by: str, top_k: int, filter_tags: Optional[list]
    ) -> dict:
        top_k = _clamp_limit(top_k)
        return _write_message_key(await self._post_dict(
            f"/api/agents/{agent_id}/social-network/stats",
            json={"sort_by": sort_by, "top_k": top_k, "filter_tags": filter_tags},
            failure_extra={"results": []},
        ))

    async def create_agent(
        self, creator_agent_id: str, new_agent_id: str, agent_name: str,
        awareness: str, agent_description: str,
    ) -> dict:
        # The route provisions the SAME caller-minted new_agent_id and shapes the
        # response with the shared format_create_agent_success, so a 2xx body
        # already matches DirectStore. _write_message_key maps the route's
        # error-keyed failures (and transport degradations) back to the tool's
        # message key.
        return _write_message_key(await self._post_dict(
            f"/api/agents/{creator_agent_id}/social-network/create-agent",
            json={
                "new_agent_id": new_agent_id,
                "agent_name": agent_name,
                "awareness": awareness,
                "agent_description": agent_description,
            },
            failure_extra={},
        ))

    # basic_info narrative/event reads. The narrative routes return the tool
    # dict shape verbatim (success/error keys — same as the tool), and failures
    # already use `error`, which is exactly _parse_dict's degradation key, so no
    # remapping is needed (unlike social's message/error). 2xx bodies pass
    # straight through.
    async def view_narrative(self, agent_id: str, narrative_id: str) -> dict:
        return await self._get_dict(
            f"/api/agents/{agent_id}/narratives/{_seg(narrative_id)}", params={}, failure_extra={},
        )

    async def view_event(self, agent_id: str, event_id: str) -> dict:
        return await self._get_dict(
            f"/api/agents/{agent_id}/events/{_seg(event_id)}", params={}, failure_extra={},
        )

    async def switch_narrative(self, agent_id: str, narrative_id: str) -> dict:
        return await self._post_dict(
            f"/api/agents/{agent_id}/narratives/{_seg(narrative_id)}/switch", json={}, failure_extra={},
        )

    # Job reads: the routes return the tool dict shape verbatim (success/error
    # keys — same as the tools and _parse_dict's degradation key), so 2xx bodies
    # pass straight through and no remap is needed. limit clamped to the route
    # bound so an over-limit never becomes a 422.
    async def job_retrieval_by_id(self, agent_id: str, job_id: str) -> dict:
        return await self._get_dict(f"/api/agents/{agent_id}/jobs/{_seg(job_id)}", params={}, failure_extra={})

    async def job_retrieval_semantic(
        self, agent_id: str, query: str, user_id: Optional[str], status: Optional[str], limit: int
    ) -> dict:
        reject = _job_query_reject(query)
        if reject is not None:
            return reject
        return await self._post_dict(
            f"/api/agents/{agent_id}/jobs/search-semantic",
            json={"query": query, "user_id": user_id, "status": status, "limit": _clamp_limit(limit)},
            failure_extra={},
        )

    async def job_retrieval_by_keywords(
        self, agent_id: str, keywords: list, user_id: Optional[str], status: Optional[str], limit: int
    ) -> dict:
        reject = _job_keywords_reject(keywords)
        if reject is not None:
            return reject
        return await self._post_dict(
            f"/api/agents/{agent_id}/jobs/search-keywords",
            json={"keywords": keywords, "user_id": user_id, "status": status, "limit": _clamp_limit(limit)},
            failure_extra={},
        )

    async def job_update(self, agent_id: str, job_id: str, fields: dict) -> dict:
        # The route calls the SAME update_job_from_args and returns its
        # message-keyed dict verbatim, so a 2xx body already matches DirectStore.
        # job_update's contract is the `message` key (never `error`), so a
        # transport degradation (_parse_dict's `error`) is remapped back to
        # `message` (with job_id) via _write_message_key.
        return _write_message_key(await self._post_dict(
            f"/api/agents/{agent_id}/jobs/{_seg(job_id)}/update",
            json=fields,
            failure_extra={"job_id": job_id},
        ))

    async def get_chat_history(self, agent_id: str, instance_id: str, limit: int) -> dict:
        # The route returns the EXACT dict fetch_chat_history produces, so a 2xx
        # body is returned verbatim. Transport degradations fall back to the
        # tool's own failure shape (never an exception). instance_id travels in
        # the body, not the path, so no percent-encoding is needed.
        return await self._post_dict(
            f"/api/agents/{agent_id}/chat-history/by-instance",
            json={"instance_id": instance_id, "limit": limit},
            failure_extra={"instance_id": instance_id, "total_messages": 0, "messages": []},
        )

    async def _send(self, method: str, path: str, **kw):
        """Transport layer shared by every Http method: one AsyncClient + one
        HTTPError boundary. Returns the httpx.Response, or None when the backend
        is unreachable — each caller maps None to its own in-band failure (dict
        for the data methods, str for awareness), since DirectStore never
        raises. Keeps request/response handling (parse, status mapping) in the
        callers so awareness's str contract and the data methods' dict contract
        don't have to share a parser."""
        import httpx

        try:
            async with httpx.AsyncClient(
                base_url=self._base, headers=self._headers, timeout=20.0
            ) as c:
                return await c.request(method, path, **kw)
        except httpx.HTTPError as e:
            logger.warning(f"[data-access] backend unreachable {method} {path}: {e}")
            return None

    async def _get_dict(self, path: str, *, params: dict, failure_extra: dict) -> dict:
        r = await self._send("GET", path, params=params)
        if r is None:
            return {"success": False, "error": "backend unreachable", **failure_extra}
        return self._parse_dict(r, path, failure_extra)

    async def _post_dict(self, path: str, *, json: dict, failure_extra: dict) -> dict:
        r = await self._send("POST", path, json=json)
        if r is None:
            return {"success": False, "error": "backend unreachable", **failure_extra}
        return self._parse_dict(r, path, failure_extra)

    @staticmethod
    def _parse_dict(r, path: str, failure_extra: dict) -> dict:
        # 422 = FastAPI rejected the arguments against the route's pydantic
        # bounds BEFORE the handler ran. HttpStore pre-validates the
        # normalizable subset so this is rare, but surface it as an ACTIONABLE
        # message (not lumped with 401/502) so the agent can fix its arguments
        # rather than read "backend rejected" and blind-retry.
        if r.status_code == 422:
            logger.warning(f"[data-access] backend rejected arguments {path}: 422")
            return {
                "success": False,
                "error": "invalid arguments (an argument is out of the route's allowed range)",
                **failure_extra,
            }
        # Any other non-2xx is a transport/middleware rejection (the handlers
        # answer 200; e.g. the Q6 identity 401). Surface it as the tool's own
        # failure dict, never an exception.
        if r.status_code >= 400:
            logger.warning(f"[data-access] backend rejected {path}: {r.status_code}")
            return {"success": False, "error": f"backend rejected the call ({r.status_code})", **failure_extra}
        try:
            return r.json() or {"success": False, "error": "empty response", **failure_extra}
        except ValueError:
            return {"success": False, "error": "non-JSON response", **failure_extra}
