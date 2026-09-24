---
code_file: src/narranexus/platform/browser/_browser_impl/approval_store.py
last_verified: 2026-09-24
stub: false
---

# Cross-process approval persistence

只列出和处理受支持能力的请求。历史 access 通知不再显示，get/take/resolve 同样不能
消费或恢复这些记录；不删除历史数据或改变数据库结构。登录通知由独立 repository 处理。

The MCP host asks and the backend answers, so questions and grants must be
visible across processes. Canonical request identity includes both turn and
conversation; a deterministic approval ID uses the existing unique index to
deduplicate simultaneous requests without a schema migration. The public shape
exposes that approval ID and allowed_lifetimes, never the database primary key.
After a settings reset requires a new answer in the same scope, request identity
advances deterministically past persisted receipts. This retains cross-process
deduplication while keeping consumed or revoked prompts permanently non-replayable.

Resolution reads fresh policy and uses a binary compare-and-swap against its
serialized document, conditioned on the owned prompt still existing. Competing
answers retry against the new policy instead of overwriting each other's grants.
A decision receipt is stored in the same atomic JSON update as the permission.
Pending-row deletion happens afterward: failure or a crash cannot lose a decision
or let another tab replay it. Pending listings exclude receipted requests, and a
failed policy write leaves the prompt available.

This deliberately avoids opening a transaction on SQLite's shared connection:
that adapter's transaction state is connection-wide and could capture unrelated
writes. The same compare-and-swap also works on MySQL. Receipts are persisted
tombstones; they must not be discarded while a matching request could be replayed.
The route checks ownership before calling resolve, and resolve checks the agent
again. Corrupt policy data fails closed instead of replacing a deny with defaults.
