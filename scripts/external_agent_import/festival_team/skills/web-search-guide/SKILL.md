---
name: web-search-guide
description: How to verify facts with web search. Trigger when the user asks for current information, when you need to confirm festival programme details, or before writing any specific claim about named events / speakers / partner-museum programming. NEVER fabricate web-search results; if no tool is available, say so explicitly.
---

# Web Search — usage guide

## When to invoke

- Verifying a specific named event ("is there a panel on AI at the 2026 festival?")
- Pulling partner-museum programming for the festival weekend
- Checking transport / accessibility info for the venue
- Confirming any specific claim about programming, speakers, or scheduling

## Available search paths (in order of preference)

1. **Built-in WebSearch tool** (NarraNexus on Claude / SDK): just call it natively.
2. **Tavily MCP server** (if installed): use `tavily_search` tool. Requires `TAVILY_API_KEY` env var.
3. **Exa MCP server** (if installed): use `exa_search` tool. Requires `EXA_API_KEY` env var.
4. **Fetch URL via curl** (fallback): `bash curl -sL "https://www.imperial.ac.uk/festival/"` and parse the HTML.

## Authoritative sources for this team

When checking festival facts, prefer in order:

1. `https://www.imperial.ac.uk/festival/` — official festival page
2. `https://www.imperial.ac.uk/be-inspired/festival/` — companion Imperial pages
3. `https://www.sciencemuseum.org.uk/`, `https://www.nhm.ac.uk/`, `https://www.vam.ac.uk/`, `https://www.royalalberthall.com/` — partner museums for their festival-weekend programming

## Rules

- **Cite sources**: when reporting back, include the URL you found the fact at.
- **Date-stamp**: prefix volatile facts with the verification date ("as of 2026-06-02").
- **Don't hallucinate**: if a search returns no useful result, say so. "I could not confirm a specific X event in the public pages" beats inventing one.
- **Don't pull live tickets / prices**: festival is free; don't add fake pricing.

## Quick patterns

- "Verify that the festival is free in 2026" → search festival page → confirm "free annual celebration" string is present.
- "What's at the Science Museum that weekend?" → search `site:sciencemuseum.org.uk great exhibition road festival 2026`.
- "Who designed the festival branding?" → search; if unknown, say unknown rather than guess.
