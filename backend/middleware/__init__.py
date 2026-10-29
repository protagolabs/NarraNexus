"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-04-28
@description: HTTP middleware package, plus the recorders those middlewares
call into.

Each middleware lives in its own module so it can be picked up or
omitted independently. Not every module here is itself an ASGI middleware:
``login_ips`` is the recorder the auth middleware (and the WebSocket auth,
which no HTTP middleware sees) calls to note a user's public client IP. Importers should pull the callable directly
from the submodule, not from this package's namespace, to keep the
dependency graph explicit.
"""
