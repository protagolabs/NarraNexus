---
code_file: backend/run_lifecycle.py
last_verified: 2026-09-23
stub: false
---

# Run Task Ownership

Uvicorn waits for ASGI handlers, but agent execution deliberately outlives its
WebSocket or HTTP request. The backend must therefore own those detached tasks
and await their complete lifetime before releasing database and worker resources.
The run-ID lookup alone misses runs still waiting for admission or Step 0.

`RunTasks.start` retains tasks synchronously at creation and observes exceptions.
`close` refuses further starts, drains every retained task without cancelling it,
then invokes dependency cleanup once. Its thirty-second wait interval only logs
progress; it is not an agent deadline. Failed or cancelled tasks do not abandon
their peers. Repeated asyncio cancellation and AnyIO cancel scopes cannot let a
shutdown caller escape before drain and resource cleanup finish.

This protects the backend lifespan's graceful-stop path. It cannot survive
SIGKILL or an external supervisor stopping MCP/database dependencies early.
The native launcher's existing three-second kill fallback and unordered stop-all,
and dev-local.sh's group stop, remain outside this guarantee.
