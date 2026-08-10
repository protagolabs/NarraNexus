---
code_file: src/xyz_agent_context/module/social_network_module/__init__.py
last_verified: 2026-08-10
stub: false
---

# social_network_module/__init__.py — package surface

Re-exports the SocialNetworkModule package's public symbols. Beyond the module
class it also surfaces the small shared helpers the AgentDataStore seam and the
backend `/social-network/*` routes both import so the two migration paths stay
byte-identical (single source, no drift):

- `social_instance_not_found_msg` — the one canonical "agent has no
  SocialNetworkModule instance" string (PR-4).
- `format_contact_result` / `format_stats_result` — the get_contact_info /
  get_agent_social_stats tools' result-shaping, hoisted here so DirectStore and
  the read twin routes share it (PR-5).

See [[social_network_module]] for where these live and why.
