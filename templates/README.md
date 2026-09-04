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
| ui_page / ui_panel / theme | `frontend.ui` declarations + a `frontend/src/index.ts` built with `@narranexus/sdk` |
