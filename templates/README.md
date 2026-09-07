# Plugin templates

One directory per contribution kind. `narranexus plugin new <publisher>.<name> --kinds routes,table,...`
composes them into one plugin: manifest fragments deep-merge, `backend/__init__.py`
fragments concatenate, files copy with `__PLUGIN_ID__` / `__PLUGIN_PKG__` /
`__DISPLAY_NAME__` / `__TABLE_PREFIX__` substituted. Every template carries a test
that runs against `narranexus.sdk.testing.PluginTestHost`.

| kind | contributes |
|---|---|
| routes | `backend.routes` (mounted under `/api/x/<id>`) |
| table | `backend.tables` (`ext_<id>_` prefix) |
| worker | `backend.workers` (workers process) |
| hook | `backend.hooks` (`@hookimpl`) |
| settings | `backend.settings` (typed, secrets encrypted) |
| tool | `agent.capabilities.tools` + a stdio MCP server |
| mcp_server | `agent.capabilities.mcp_servers` (remote) |
| bundle | `content.bundles` (`.nxbundle`) |
| skill | `content.skills` (`SKILL.md`) |
| stage_strategy | `turn.pipeline.recall` (a Recall strategy; the same shape fits every stage slot) |
| pipeline_profile | `turn.profiles` (names the strategy each stage runs) |
| context_provider | `agent.capabilities.context_providers` (Assemble-only prompt contributions) |
| channel | `ingress.channels` + `ingress.triggers` + `agent.capabilities.modules` (an IM channel: descriptor, webhook trigger, send-tool module) |
| ui_page / ui_panel / theme | `frontend.ui` declarations + a `frontend/src/index.ts` built with `@narranexus/sdk` |

## Naming conventions

The symbols a manifest names are UPPER_SNAKE plural nouns of what they hold:
`ROUTES`, `TABLES`, `WORKERS`, `HOOKS`, `SETTINGS`, `TOOLS`, `MCP_SERVERS`,
`BUNDLES`, `SKILLS`, `RECALL_STRATEGIES`, `PROFILES`, `CONTEXT_PROVIDERS`,
`CHANNEL` / `TRIGGERS` / `MODULES` — tuples of `Contribution` (hook functions
for `HOOKS`). Builtin plugins keep the same vocabulary in their
`contribution.py` (`CONTRIBUTION` for a one-arity slot, `CONTRIBUTIONS` for a
many-arity one, `MODULES` / `TRIGGERS` for channels). `docs/API_POLICY.md` §7.
