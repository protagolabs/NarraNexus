"""
@file_name: output_transfer.py
@author: NetMind.AI
@date: 2025-11-15
@description: 将不同 Agent SDK 的输出转换为统一的 OpenAI Agents SDK 格式。

返回值为 List[Dict]，因为一条 SDK 消息可能包含多个内容块（如多个 ToolUseBlock），
每个块对应一个独立事件。
"""

import json
from typing import Any, Dict, List


def _stringify_tool_result_content(content: Any) -> str:
    """Flatten a ToolResultBlock.content into the tool's plain-text payload.

    The Claude Agent SDK delivers a tool result either as a bare string or as
    a list of content blocks — dicts shaped like ``{"type": "text", "text":
    ...}`` or SDK block objects exposing a ``.text`` attribute. An MCP tool
    that returns a dict (e.g. ``register_artifact``) arrives as a single text
    block whose ``text`` is the JSON-encoded result.

    The previous implementation used ``str(block.content)``, which on a list
    produces a Python repr (``[{'type': 'text', 'text': '...'}]``) — NOT valid
    JSON. Every frontend consumer that ``JSON.parse``-s ``tool_output``
    (artifact discovery, quota-error detection) silently failed on it, so
    agent-created artifacts never surfaced until an unrelated reload. This
    flattens to the actual text payload so the result stays parseable.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                parts.append(
                    text if isinstance(text, str)
                    else json.dumps(item, ensure_ascii=False)
                )
            elif hasattr(item, "text"):
                parts.append(str(item.text))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


def output_transfer(
    message: Any,
    transfer_type: str = "claude_agent_sdk",
    streaming: bool = True
) -> List[Dict[str, Any]]:
    """
    将 Agent SDK 输出转换为 OpenAI Agents SDK 格式。

    返回事件列表（一条 SDK 消息可能产生多个事件，如并行工具调用）。

    Args:
        message: Agent SDK 消息对象
        transfer_type: 转换类型
        streaming: 是否流式输出

    Returns:
        事件字典列表
    """
    if transfer_type == "claude_agent_sdk":
        return _claude_to_openai_agents(message, streaming=streaming)
    if transfer_type == "codex_cli":
        return _codex_to_openai_agents(message, streaming=streaming)
    else:
        raise ValueError(f"Unknown transfer type: {transfer_type}")


def _claude_to_openai_agents(message: Any, streaming: bool = True) -> List[Dict[str, Any]]:
    """
    将 Claude Agent SDK 消息转换为 OpenAI Agents SDK 格式。

    返回事件列表。大多数消息产生一个事件，但 AssistantMessage（多个 ToolUseBlock）
    和 UserMessage（多个 ToolResultBlock）可产生多个事件。
    """
    message_type = type(message).__name__

    if streaming:
        return _convert_to_streaming_events(message, message_type)
    else:
        return [_convert_to_non_streaming_result(message, message_type)]


def _convert_to_streaming_events(message: Any, message_type: str) -> List[Dict[str, Any]]:
    """将 Claude 消息转换为流式事件列表。"""

    if message_type == "AssistantMessage":
        return _convert_assistant_to_stream_events(message)
    elif message_type == "StreamEvent":
        return [_convert_stream_event_to_stream_event(message)]
    elif message_type == "ResultMessage":
        return [_convert_result_to_stream_event(message)]
    elif message_type == "SystemMessage":
        return [_convert_system_to_stream_event(message)]
    elif message_type == "UserMessage":
        return _convert_user_to_stream_events(message)
    else:
        return [{
            "type": "raw_response_event",
            "data": {
                "type": "response.text.delta",
                "delta": f"[Unknown message type: {message_type}]"
            }
        }]


def _convert_to_non_streaming_result(message: Any, message_type: str) -> Dict[str, Any]:
    """Convert Claude message to OpenAI Agents SDK non-streaming result format."""

    # For non-streaming, we accumulate all content and return a RunResult-like structure
    result = {
        "final_output": "",
        "new_items": [],
        "usage": {}
    }

    if message_type == "AssistantMessage":
        # Extract text content
        text_parts = []
        tool_calls = []

        if hasattr(message, 'content') and message.content:
            for block in message.content:
                block_type = type(block).__name__

                if block_type == "TextBlock" and hasattr(block, 'text'):
                    text_parts.append(block.text)
                    result["new_items"].append({
                        "type": "message_output_item",
                        "content": block.text
                    })
                elif block_type == "ThinkingBlock" and hasattr(block, 'thinking'):
                    # Optionally include thinking
                    result["new_items"].append({
                        "type": "thinking_item",
                        "content": block.thinking
                    })
                elif block_type == "ToolUseBlock":
                    if hasattr(block, 'id') and hasattr(block, 'name') and hasattr(block, 'input'):
                        tool_call = {
                            "type": "tool_call_item",
                            "tool_call_id": block.id,
                            "tool_name": block.name,
                            "arguments": block.input
                        }
                        tool_calls.append(tool_call)
                        result["new_items"].append(tool_call)

        result["final_output"] = "\n".join(text_parts) if text_parts else ""

    elif message_type == "ResultMessage":
        # Add usage information
        # ResultMessage.usage is dict[str, Any] | None (not an object with attributes)
        raw_usage = getattr(message, 'usage', None)
        if isinstance(raw_usage, dict):
            if "input_tokens" in raw_usage:
                result["usage"]["input_tokens"] = raw_usage["input_tokens"]
            if "output_tokens" in raw_usage:
                result["usage"]["output_tokens"] = raw_usage["output_tokens"]
            if result["usage"]:
                result["usage"]["total_tokens"] = (
                    result["usage"].get("input_tokens", 0) +
                    result["usage"].get("output_tokens", 0)
                )

        # Add stop reason
        if hasattr(message, 'stop_reason'):
            result["stop_reason"] = message.stop_reason

    return result


def _convert_assistant_to_stream_events(message: Any) -> List[Dict[str, Any]]:
    """将 Claude AssistantMessage 转换为流式事件列表。

    include_partial_messages=True 时，文本和思考内容会到达两次：
    先通过 StreamEvent（逐 token），再通过完整 AssistantMessage。
    因此这里跳过 TextBlock 和 ThinkingBlock（已经流式过了），
    只提取所有 ToolUseBlock 事件（用于 Steps 面板）。

    注意：partial AssistantMessage 也会携带 ToolUseBlock，导致同一个 tool_call_id
    出现多次。去重逻辑在 xyz_claude_agent_sdk.py 的 agent_loop 中处理。
    """

    # Check AssistantMessage.error field (auth failure, quota exhaustion, rate limit, etc.)
    # SDK defines: "authentication_failed" | "billing_error" | "rate_limit" |
    #              "invalid_request" | "server_error" | "unknown"
    if hasattr(message, 'error') and message.error is not None:
        error_type = str(message.error)  # Already a standardized literal from SDK

        # Human-readable messages for each error type
        error_messages = {
            "rate_limit": "Claude API rate limit reached. Please wait a moment and try again.",
            "authentication_failed": "Claude API authentication failed. Please check your API key.",
            "billing_error": "Claude API billing error. Please check your account credits.",
            "invalid_request": "Claude API received an invalid request.",
            "server_error": "Claude API server error. Please try again later.",
        }
        error_message = error_messages.get(error_type, f"Claude API error: {error_type}")

        return [{
            "type": "raw_response_event",
            "data": {
                "type": "response.error",
                "error_message": error_message,
                "error_type": error_type,
            }
        }]

    if not hasattr(message, 'content') or not message.content:
        return [_empty_delta()]

    # 提取所有 ToolUseBlock，每个生成一个 tool_call_item 事件
    events: List[Dict[str, Any]] = []
    for block in message.content:
        block_type = type(block).__name__

        if block_type == "ToolUseBlock":
            if hasattr(block, 'id') and hasattr(block, 'name') and hasattr(block, 'input'):
                events.append({
                    "type": "run_item_stream_event",
                    "item": {
                        "type": "tool_call_item",
                        "tool_call_id": block.id,
                        "tool_name": block.name,
                        "arguments": block.input
                    }
                })
        # TextBlock, ThinkingBlock → 跳过（已通过 StreamEvent 流式传输）

    # 没有 ToolUseBlock 时返回空 delta（内容已通过 StreamEvent 流式传输）
    return events if events else [_empty_delta()]


def _convert_stream_event_to_stream_event(message: Any) -> Dict[str, Any]:
    """Convert Claude StreamEvent to OpenAI Agents SDK stream event.

    With include_partial_messages=True, StreamEvent carries an `event` dict
    containing Anthropic API streaming events (content_block_delta, etc.).
    We extract text and thinking deltas and forward them to the frontend.
    """

    event = getattr(message, 'event', None)
    if not isinstance(event, dict):
        # Fallback for unexpected format
        return {
            "type": "raw_response_event",
            "data": {
                "type": "response.text.delta",
                "delta": ""
            }
        }

    event_type = event.get("type", "")

    if event_type == "content_block_delta":
        delta = event.get("delta", {})
        delta_type = delta.get("type", "")

        if delta_type == "text_delta":
            return {
                "type": "raw_response_event",
                "data": {
                    "type": "response.text.delta",
                    "delta": delta.get("text", "")
                }
            }

        if delta_type == "thinking_delta":
            return {
                "type": "run_item_stream_event",
                "item": {
                    "type": "thinking_item",
                    "content": delta.get("thinking", "")
                }
            }

        # input_json_delta, signature_delta → skip (empty content)
        return {
            "type": "raw_response_event",
            "data": {
                "type": "response.text.delta",
                "delta": ""
            }
        }

    # Structural events (content_block_start/stop, message_start/delta/stop) → skip
    return {
        "type": "raw_response_event",
        "data": {
            "type": "response.text.delta",
            "delta": ""
        }
    }


def _convert_result_to_stream_event(message: Any) -> Dict[str, Any]:
    """Convert Claude ResultMessage to OpenAI Agents SDK stream event (completion marker)."""

    # Result message typically marks the end of the stream
    # We represent this as a raw_response_event with type "response.done"
    data = {
        "type": "response.done",
    }

    # Add usage info if available
    # ResultMessage.usage is dict[str, Any] | None (not an object with attributes)
    raw_usage = getattr(message, 'usage', None)
    if isinstance(raw_usage, dict):
        usage = {}
        if "input_tokens" in raw_usage:
            usage["input_tokens"] = raw_usage["input_tokens"]
        if "output_tokens" in raw_usage:
            usage["output_tokens"] = raw_usage["output_tokens"]
        # Include cache tokens for accurate cost calculation
        if "cache_creation_input_tokens" in raw_usage:
            usage["cache_creation_input_tokens"] = raw_usage["cache_creation_input_tokens"]
        if "cache_read_input_tokens" in raw_usage:
            usage["cache_read_input_tokens"] = raw_usage["cache_read_input_tokens"]
        if usage:
            usage["total_tokens"] = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            data["usage"] = usage

        # Extract model name from usage dict if CLI provides it
        if "model" in raw_usage:
            data["model"] = raw_usage["model"]

    # Claude Agent SDK doesn't expose the actual model name.
    # Use "claude-code" as an honest label; cost comes from sdk_cost_usd, not price table.
    if "model" not in data:
        data["model"] = "claude-code"

    # Add SDK-calculated cost (ResultMessage.total_cost_usd)
    total_cost = getattr(message, 'total_cost_usd', None)
    if total_cost is not None:
        data["total_cost_usd"] = total_cost

    # Add stop reason
    if hasattr(message, 'stop_reason'):
        data["stop_reason"] = message.stop_reason

    return {
        "type": "raw_response_event",
        "data": data
    }


def _convert_system_to_stream_event(message: Any) -> Dict[str, Any]:
    """Convert Claude SystemMessage to OpenAI Agents SDK stream event."""

    content = ""
    if hasattr(message, 'metadata'):
        content = f"[System: {message.metadata}]"

    return {
        "type": "raw_response_event",
        "data": {
            "type": "response.text.delta",
            "delta": content
        }
    }


def _convert_user_to_stream_events(message: Any) -> List[Dict[str, Any]]:
    """将 Claude UserMessage 转换为流式事件列表。

    一条 UserMessage 可能包含多个 ToolResultBlock（并行工具调用的结果），
    每个 ToolResultBlock 生成一个独立的 tool_call_output_item 事件。
    """

    events: List[Dict[str, Any]] = []
    text_parts: list[str] = []

    if hasattr(message, 'content') and message.content:
        for block in message.content:
            block_type = type(block).__name__

            if block_type == "TextBlock" and hasattr(block, 'text'):
                text_parts.append(block.text)
            elif block_type == "ToolResultBlock" and hasattr(block, 'content'):
                events.append({
                    "type": "run_item_stream_event",
                    "item": {
                        "type": "tool_call_output_item",
                        "output": _stringify_tool_result_content(block.content)
                    }
                })

    # 如果有 ToolResultBlock 事件，直接返回（文本内容通常是内部消息，不需要展示）
    if events:
        return events

    # 没有 ToolResultBlock 时，返回文本内容
    content = "\n".join(text_parts) if text_parts else ""
    return [{
        "type": "raw_response_event",
        "data": {
            "type": "response.text.delta",
            "delta": content
        }
    }]


def _empty_delta() -> Dict[str, Any]:
    """返回空的 text delta 事件"""
    return {
        "type": "raw_response_event",
        "data": {
            "type": "response.text.delta",
            "delta": ""
        }
    }


# =============================================================================
# Codex CLI translator
# =============================================================================
#
# Codex CLI emits JSON Lines on stdout when invoked with --json. Each line
# is a complete JSON object with a ``type`` field. Event surface (from
# docs https://developers.openai.com/codex/noninteractive):
#
#   thread.started        — turn-bracket marker (info-only)
#   turn.started          — turn-bracket marker (info-only)
#   item.started          — agentic item begins (text, reasoning, tool call,
#                           file change, MCP call, web search)
#   item.completed        — item finishes
#   turn.completed        — usage payload arrives here
#   turn.failed           — non-recoverable turn error
#   error                 — top-level fatal error
#
# We translate these into the OpenAI Agents SDK event shape that
# response_processor.process already consumes, so downstream code is
# unchanged. Unknown event types are dropped (forward-compat) rather
# than raised — Codex may introduce new event types in future releases.

# Codex item ``type`` values we know how to translate. Anything not
# listed here is silently dropped with a debug log on the wrapper side.
_CODEX_ITEM_TYPES_TEXT = frozenset({"agent_message"})
_CODEX_ITEM_TYPES_THINKING = frozenset({"reasoning"})
_CODEX_ITEM_TYPES_TOOL = frozenset({"command_execution", "mcp_tool_call", "web_search"})


def _codex_to_openai_agents(
    event: Dict[str, Any], streaming: bool = True
) -> List[Dict[str, Any]]:
    """Translate one parsed Codex JSON Lines event into 0..N
    OpenAI-Agents-style events that ``response_processor`` consumes.

    Args:
        event: A dict parsed from a single line of ``codex exec --json``
            stdout. Must carry a ``type`` field; anything else is
            treated as unknown.
        streaming: Currently ignored — Codex's `item.started` /
            `item.completed` pair already encodes the streaming
            boundary. Kept for signature symmetry with the Claude
            translator.

    Returns:
        List of event dicts shaped like ``{"type": "raw_response_event"}``
        or ``{"type": "run_item_stream_event"}``. Empty list means
        "drop this event" (info-only or unknown).
    """
    del streaming  # signature-only — see note above

    if not isinstance(event, dict):
        return []

    event_type = event.get("type")

    # ---- turn / thread brackets — info only, drop ---------------------
    if event_type in {"thread.started", "turn.started"}:
        return []

    # ---- item.started / item.completed -------------------------------
    if event_type in {"item.started", "item.completed"}:
        return _translate_codex_item(event, is_completed=(event_type == "item.completed"))

    # ---- turn.completed — usage payload ------------------------------
    if event_type == "turn.completed":
        usage = event.get("usage") or {}
        # Mirror Claude's ResultMessage translation: response_processor
        # only folds usage from raw_response_event/response.done.
        return [{
            "type": "raw_response_event",
            "data": {
                "type": "response.done",
                "usage": {
                    "input_tokens": int(usage.get("input_tokens") or 0),
                    "output_tokens": int(usage.get("output_tokens") or 0),
                    "cached_input_tokens": int(usage.get("cached_input_tokens") or 0),
                },
                "stop_reason": event.get("reason") or "completed",
            },
        }]

    # ---- failures ----------------------------------------------------
    if event_type in {"turn.failed", "error"}:
        # response_processor doesn't have a dedicated error type; we
        # surface as a generic raw_response_event with the error text
        # so the user sees something. The wrapper also logs at ERROR
        # level when it sees these.
        err = event.get("error") or event.get("message") or "unknown"
        return [{
            "type": "raw_response_event",
            "data": {
                "type": "response.error",
                "error_message": str(err),
                "error_type": event_type,
            },
        }]

    # Unknown — drop with no noise (forward-compat with future Codex
    # event types). The wrapper logs at DEBUG so ops can spot novel
    # types if they ever matter.
    return []


def _translate_codex_item(
    event: Dict[str, Any], *, is_completed: bool
) -> List[Dict[str, Any]]:
    """Handle one ``item.started`` or ``item.completed`` Codex event."""
    item = event.get("item") or {}
    if not isinstance(item, dict):
        return []

    item_type = item.get("type")
    item_id = item.get("id") or ""

    # ---- text from the model -----------------------------------------
    if item_type in _CODEX_ITEM_TYPES_TEXT:
        text = item.get("text") or ""
        # Both started and completed carry the same delta text from
        # Codex's perspective — we only emit on completed to avoid
        # double-rendering (Codex's `item.started` for agent_message
        # arrives with empty text; the full text only lands on
        # `item.completed`).
        if not is_completed or not text:
            return []
        return [{
            "type": "raw_response_event",
            "data": {
                "type": "response.text.delta",
                "delta": text,
            },
        }]

    # ---- reasoning ("thinking") --------------------------------------
    if item_type in _CODEX_ITEM_TYPES_THINKING:
        text = item.get("text") or ""
        if not is_completed or not text:
            return []
        # Map to the same thinking item shape as Claude's StreamEvent
        # translator so response_processor's _ThinkingBatcher sees it.
        return [{
            "type": "run_item_stream_event",
            "item": {
                "type": "thinking_item",
                "content": text,
            },
        }]

    # ---- tool calls (command_execution / mcp_tool_call / web_search) -----
    if item_type in _CODEX_ITEM_TYPES_TOOL:
        # Codex emits item.started AND item.completed for tool items.
        # We translate BOTH so the frontend can show "tool running"
        # state on started + "tool output" on completed.
        if not is_completed:
            # Start: emit tool_call_item with args, no output yet.
            # IMPORTANT: response_processor._handle_run_item_stream_event
            # reads ``item.get("tool_name", "unknown")``, NOT
            # ``item.get("name", ...)``. Using the wrong key silently
            # records every tool call as "unknown" and the frontend
            # filters / hides them after the run completes.
            return [{
                "type": "run_item_stream_event",
                "item": {
                    "type": "tool_call_item",
                    "tool_call_id": item_id,
                    "tool_name": _codex_tool_name(item),
                    "arguments": _codex_tool_args(item),
                },
            }]
        # Completed: emit tool_call_output_item (deduped on
        # tool_call_id by the wrapper's seen_tool_call_ids set). The
        # output handler in response_processor looks up the matching
        # tool_name from the previously-recorded tool_call step in
        # state.all_steps, so consistency with the started side
        # matters for the post-run rendering path.
        return [{
            "type": "run_item_stream_event",
            "item": {
                "type": "tool_call_output_item",
                "tool_call_id": item_id,
                "tool_name": _codex_tool_name(item),
                "output": _codex_tool_output(item),
                "status": item.get("status") or "completed",
            },
        }]

    # Unknown item type — drop. Forward-compat.
    return []


def _codex_tool_name(item: Dict[str, Any]) -> str:
    """Name to surface for the tool_call_item, by Codex item subtype."""
    item_type = item.get("type") or ""
    if item_type == "command_execution":
        return "Bash"  # render as Bash to match CC's tool naming
    if item_type == "mcp_tool_call":
        server = item.get("server") or ""
        tool = item.get("tool") or ""
        return f"mcp__{server}__{tool}" if server and tool else "mcp"
    if item_type == "web_search":
        return "WebSearch"
    return item_type or "tool"


def _codex_tool_args(item: Dict[str, Any]) -> Dict[str, Any]:
    """Tool arguments shape for tool_call_item, by Codex item subtype."""
    item_type = item.get("type") or ""
    if item_type == "command_execution":
        return {"command": item.get("command") or ""}
    if item_type == "mcp_tool_call":
        # MCP tool args arrive as a dict; pass through.
        args = item.get("arguments")
        if isinstance(args, dict):
            return args
        return {}
    if item_type == "web_search":
        return {"query": item.get("query") or ""}
    return {}


def _codex_tool_output(item: Dict[str, Any]) -> str:
    """Tool output payload string for tool_call_output_item.

    Field names observed from real ``codex exec --json`` output
    (2026-06-01 log capture):

    - ``command_execution``: stdout+stderr is in **``aggregated_output``**
      (NOT ``output`` as the public docs suggest). ``exit_code`` is an
      integer on completed items; we surface it when the command failed
      so the agent can see the non-zero status alongside any captured
      text.
    - ``mcp_tool_call``: result payload in ``result`` (dict / list / string).
    - ``web_search``: ``results`` list of {title, url, snippet}.

    Fallback to ``output`` is kept for forward-compat: if a future
    Codex CLI version renames the field back, both keys will be tried.
    """
    item_type = item.get("type") or ""
    if item_type == "command_execution":
        text = item.get("aggregated_output")
        if not text:
            text = item.get("output", "")
        body = _stringify_tool_result_content(text)
        exit_code = item.get("exit_code")
        # Surface non-zero exit codes when output is otherwise quiet —
        # otherwise the agent sees an empty box and may retry the same
        # failing command.
        if isinstance(exit_code, int) and exit_code != 0:
            if body:
                body = f"{body}\n[exit code: {exit_code}]"
            else:
                body = f"[exit code: {exit_code}]"
        return body
    if item_type == "mcp_tool_call":
        result = item.get("result")
        if isinstance(result, (dict, list)):
            return json.dumps(result, ensure_ascii=False)
        return _stringify_tool_result_content(result)
    if item_type == "web_search":
        # web_search.results is usually a list of {title, url, snippet}
        results = item.get("results")
        if results is None:
            return ""
        return json.dumps(results, ensure_ascii=False)
    return ""
