---
code_file: src/xyz_agent_context/utils/timezone.py
last_verified: 2026-08-18
stub: false
---

## 2026-08-18 — 新增 agent-facing 渲染层

原来这个文件只管两件事：内部存 UTC、对外/对前端转用户时区。这次补上第三件 —— **agent
读到的每一个时间，都必须从这里渲染出去**。

背后的规律是踩出来的：模型没法比较两个时间戳，除非它能看出这两个在**同一个 frame**。
一个值渲染成用户本地、另一个渲染成裸 UTC，在语言模型眼里读不出"这是两个坐标系"，只读得出
"这里有矛盾"，然后它会把矛盾合理化掉。所以这一节的渲染器**一律**带显式 UTC 偏移。

新增：

- `resolve_timezone(tz)` —— 统一的降级点。`users.timezone` 缺失或非法时，时间仍然正确
  （UTC），而且**标签写 "UTC"**，不会把不可用的字符串原样回显给 agent。
- `format_now_for_agent(tz)` —— `2026-04-21 17:45:08 +08:00 (Tuesday, Asia/Shanghai)`。
  从 `basic_info_module` 搬过来的：现在有三个互不相关的消费者需要同样的字节
  （BasicInfo 的 ground truth、date MCP 工具的参考点、[[temporal_guard.py]] 的比对基准），
  留在 Module 里会逼另外两个去 import 一个 Module（铁律 #3）。
- `format_timestamp_for_agent(dt, tz)` —— 渲染**已存储**的时间戳，分钟精度（每行历史都要
  带，秒是噪音）但**保留偏移**。丢掉偏移正是 UTC 存储的聊天历史被当成用户本地墙钟读的
  原因，见 [[context_runtime.py]] 2026-08-18 条目。

`WEEKDAY_NAMES` 也提到这里：星期几标签和日期必须来自同一份计算，两边各写一份迟早会飘。

# timezone.py

Timezone utilities — a consistent layer for UTC storage, user-timezone display, and LLM-friendly time formatting.

## Why it exists

Without a centralized timezone policy, `datetime.now()` calls produce naive datetimes that mix UTC and local time unpredictably across the codebase. SQLite compounds this by returning timestamps as strings. `timezone.py` establishes a single rule: all internal datetimes are UTC (created with `utc_now()`), converted to the user's IANA timezone only at display time (via `to_user_timezone`), and formatted for API responses as ISO 8601 with a `Z` suffix (via `format_for_api`) so JavaScript's `new Date()` parses them correctly.

## Upstream / Downstream

**Consumed by:** `narrative/` (formatting event timestamps for LLM prompts), `backend/routes/` (formatting timestamps in API responses), `module/` implementations that need to express times in the agent's or user's timezone. Re-exported from `utils/__init__.py`.

**Depends on:** stdlib `datetime`, `zoneinfo`. No external libraries.

## Design decisions

**`utc_now()` replaces `datetime.now()`.** Every place in the codebase that needs the current time should call `utc_now()` rather than `datetime.now()`. `utc_now()` returns a timezone-aware UTC datetime, which prevents the common bug of naive datetimes mixing with timezone-aware datetimes in arithmetic.

**`to_user_timezone` handles SQLite string inputs.** SQLite returns datetime columns as strings. Rather than requiring every caller to parse the string first, `to_user_timezone` detects string inputs, strips the trailing `Z` (if any), and calls `datetime.fromisoformat()` before converting. This makes the function safe to call with raw database values.

**`format_for_api` always outputs `Z`-suffixed ISO 8601.** The `Z` suffix is critical for JavaScript interoperability. `new Date("2025-01-15T14:30:00")` (no `Z`) is interpreted as local time in some browsers; `new Date("2025-01-15T14:30:00Z")` is always UTC.

**`format_for_llm` outputs a human-readable format with timezone abbreviation.** LLMs respond better to `"2025/1/15 PM 2:30 (Asia/Shanghai)"` than to ISO 8601. This format is intentionally non-standard because it targets the LLM's language model, not a parser.

**Validation with `is_valid_timezone`.** Timezone strings from user input are validated by attempting to construct a `ZoneInfo` object. Invalid strings produce a descriptive error rather than a runtime `KeyError` later.

## Gotchas

**Naive datetimes are assumed UTC.** Both `to_user_timezone` and `format_for_api` treat naive datetime inputs as UTC by calling `.replace(tzinfo=timezone.utc)`. If a naive datetime was actually created in a local timezone (e.g., by calling `datetime.now()` without UTC), the output will be wrong by the offset of that timezone.

**SQLite timestamp parsing uses `datetime.fromisoformat` which is strict.** Non-ISO strings (e.g., `"Jan 15, 2025"`) in timestamp columns will cause `fromisoformat` to raise `ValueError`, causing `to_user_timezone` to return `None` and `format_for_api` to return `None` or the raw string. The column must contain ISO 8601 values for these functions to work correctly.

**New-contributor trap.** `format_for_llm` returns the string `"Time unknown"` when `dt` is `None` rather than raising an error or returning `None`. Callers that check `if result:` will get truthy behavior for a missing time, which can mask missing data in prompt assembly.
