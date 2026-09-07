---
code_file: src/narranexus/platform/channel/binding_tables.py
last_verified: 2026-09-07
stub: false
---

# src/narranexus/platform/channel/binding_tables.py

## 2026-09-07 — parameterised binding sources for the agents directory

channel_binding_sources() yields one BindingSource per registered IM channel — a sub-select over channel_credentials whose channel name is a %s PARAMETER and whose alias is an index (src_0), never the name — plus the binding-only tables; bound_channels_query() assembles the UNION ALL with the channel name of each branch bound too. Moved out of bundle/ (it is a directory projection, nothing to do with bundles) and made injection-proof by construction rather than by relying on ChannelDescriptor's validator in another package. MySQL twin: tests/backend/test_auth_agents_directory_mysql.py.
