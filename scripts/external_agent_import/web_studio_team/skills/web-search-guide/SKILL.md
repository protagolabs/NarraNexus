---
name: web-search-guide
description: How to verify facts with web search before writing them into the site. Trigger when the user mentions a real-world entity (event date, organization, person, product spec) that the agent can't confirm from the project brief alone. NEVER fabricate facts; if no search tool is available, say so explicitly and ask the user.
---

# Web Search — usage guide

## When to invoke

- Before writing any specific claim about real entities the user mentioned in the project brief
- Before quoting dates / prices / specs / names that aren't in `project_brief.md`
- Confirming that links to external sites still work
- Pulling supporting context (industry stats, comparable products, etc.)

## Available search paths (in order of preference)

1. **Built-in `WebSearch` tool** (NarraNexus on Claude / SDK): just call it natively. This is the default.
2. **Tavily MCP server** (if installed): use `tavily_search` tool. Requires `TAVILY_API_KEY` env var.
3. **Exa MCP server** (if installed): use `exa_search` tool. Requires `EXA_API_KEY` env var.
4. **Fetch URL via Bash + curl** (fallback): `curl -sL "<official url>"` and parse the HTML.

## Authoritative-source heuristic

For any specific entity, prefer in order:

1. The entity's **own official site** (e.g., the user said "build a site for ACME Corp" → search `acme.com`)
2. **Wikipedia** for general facts and disambiguation
3. **Specialized authoritative sources** for the domain (academic / press / industry body)
4. **News aggregators** as last resort

## Rules

- **Cite sources**: when reporting a verified fact back, include the URL.
- **Date-stamp**: prefix volatile facts with the verification date ("as of <today>").
- **Don't hallucinate**: if a search returns no useful result, say so explicitly. "I could not confirm X — please clarify or supply a source" beats inventing.
- **Don't pull live prices / dynamic data** unless the user asked for it — they go stale immediately.

## Quick patterns

- "Verify that 'Acme Conf 2026' is on Sept 12-14" → search `"Acme Conf 2026" site:acme.com OR site:acmeconf.com` → confirm date.
- "Is the user's claim that X partners with Y current?" → search official press releases → confirm.
- "Who designed brand Z's logo?" → search; if unknown, say unknown rather than guess.

## How this skill interacts with the team

- **Content Creator** uses this skill the most — every specific copy claim should pass through web-search before going into `index.html`.
- **PM** uses it lightly — only to disambiguate the brief on first turn.
- **Visual** uses it to find reference imagery / understand a brand's existing aesthetic.
- **QA** uses it to fact-check the Content's output against authoritative sources.
