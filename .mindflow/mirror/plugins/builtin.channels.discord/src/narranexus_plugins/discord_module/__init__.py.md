---
code_file: plugins/builtin.channels.discord/src/narranexus_plugins/discord_module/__init__.py
last_verified: 2026-09-07
stub: false
---

# plugins/builtin.channels.discord/src/narranexus_plugins/discord_module/__init__.py — package marker

package marker of the Discord channel plugin; nothing is imported at package import (the manifest names the contributions, the host resolves them lazily). No code belongs here: the host boots plugins from their manifests, and an import-time registration would break the lazy-contribution rule and the distribution excludes.
