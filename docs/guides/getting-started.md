# Writing a NarraNexus plugin

A plugin is a directory with `narranexus-plugin.json`, an optional Python
`backend/` package, and an optional prebuilt `frontend/dist/plugin.js`.
Plugins are a **local** feature (desktop / `run.sh`); the cloud build ships the
standard plugin set.

## 1. Scaffold

```bash
narranexus plugin new acme.weather --kinds routes,settings,ui_page
cd acme.weather
```

Kinds: `routes`, `table`, `worker`, `hook`, `settings`, `tool`, `mcp_server`,
`bundle`, `skill`, `ui_page`, `ui_panel`, `theme` (see `templates/`). The
manifest lists what you provide:

```json
"provides": { "backend.routes": ["nxplugins.acme_weather:ROUTES"] }
```

`nxplugins.<id with _>` is your package name inside the host: import your own
modules relatively, never `narranexus.kernel`. Import contracts from
`narranexus.sdk`.

## 2. Test

```bash
uv run pytest tests -q
```

`narranexus.sdk.testing.PluginTestHost` boots a minimal host around your
directory: assert on `host.names("backend.routes")`, `host.tables`,
`host.hooks`, `await host.activate()`, `host.test_app()`.

## 3. Link and run

```bash
narranexus plugin link .        # registers in place (mode: link)
# restart NarraNexus; Settings → Plugins → User plugins shows it
narranexus plugin doctor        # what would load, what is blocked and why
```

Routes appear under `/api/x/acme.weather/…` (authenticated by default; use
`auth="none"` on a `RouterSpec` for an explicit public webhook). Tables must be
named `ext_acme_weather_*`. Settings resolve `NXP_ACME_WEATHER_<KEY>` env >
stored value > default; secrets are encrypted at rest.

## 4. Frontend

`frontend/src/index.ts` exports `definePlugin({ activate(host) { … } })`; build
it with the `@narranexus/sdk` vite preset into `frontend/dist/plugin.js` and
commit the build. Declare pages/panels/commands/themes under `frontend.ui` so
the shell shows them before your code loads; activation happens on first use.

## 5. Publish

See `publishing.md`. `narranexus plugin publish-check .` runs the checklist.

## Debugging

- Settings → Plugins → User plugins shows state, warnings, isolation reason and
  the last errors per plugin.
- Two crashes disable a plugin; two dead boots enter safe mode (user plugins
  skipped); the bisect wizard finds the culprit in O(log N) restarts.
- `narranexus plugin rollback` restores the last-known-good registry.
