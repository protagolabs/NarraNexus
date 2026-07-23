"""
@file_name: execution_state.py
@author: NetMind.AI
@date: 2025-11-28
@description: Agent execution state management

State management module extracted from AgentRuntime, responsible for tracking state during Agent Loop execution.

Design principles:
- Immutable design: each state update returns a new object for easy tracking and debugging
- Single responsibility: only responsible for state storage and updates, no business logic
"""

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ExecutionState:
    """
    Agent execution state - immutable design

    Each update returns a new state object for easy tracking and debugging.

    Attributes:
        final_output: Final text output (cumulative)
        response_count: Total number of responses received
        tool_call_count: Number of tool calls
        tool_output_count: Number of tool outputs (用于 tool_output 的 step ID 匹配)
        thinking_count: Number of thinking processes
        all_steps: Records of all execution steps

    Usage:
        >>> state = ExecutionState()
        >>> state = state.append_text("Hello ")
        >>> state = state.append_text("World!")
        >>> print(state.final_output)  # "Hello World!"
    """
    final_output: str = ""
    response_count: int = 0
    tool_call_count: int = 0
    tool_output_count: int = 0
    thinking_count: int = 0
    all_steps: tuple = field(default_factory=tuple)  # Use tuple for immutability
    input_tokens: int = 0    # Cumulative input tokens across all Agent Loop turns
    output_tokens: int = 0   # Cumulative output tokens across all Agent Loop turns
    model: str = ""          # LLM model identifier (from the last response.done event)
    total_cost_usd: float = 0.0  # SDK-calculated cost (Claude Agent SDK provides this directly)
    # Prompt-cache telemetry (cumulative, like input/output tokens). 0 = the
    # provider reported no cache activity or has no cache fields at all.
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    # Model-call count reported by the framework (ResultMessage.num_turns).
    # None = not reported — deliberately distinct from a reported 0.
    num_turns: Optional[int] = None

    def append_text(self, text: str) -> 'ExecutionState':
        """
        Append text output, returns a new state object

        Args:
            text: Text to append

        Returns:
            New ExecutionState object
        """
        return replace(
            self,
            final_output=self.final_output + text,
            response_count=self.response_count + 1,
        )

    def increment_response(self) -> 'ExecutionState':
        """
        Increment response count, returns a new state object

        Returns:
            New ExecutionState object
        """
        return replace(self, response_count=self.response_count + 1)

    def record_tool_call(self, tool_name: str, tool_call_id: str, arguments: Dict[str, Any]) -> 'ExecutionState':
        """
        Record a tool call, returns a new state object

        Args:
            tool_name: Tool name
            tool_call_id: Tool call ID
            arguments: Tool arguments

        Returns:
            New ExecutionState object
        """
        new_step = {
            "type": "tool_call",
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "arguments": arguments,
        }
        return replace(
            self,
            response_count=self.response_count + 1,
            tool_call_count=self.tool_call_count + 1,
            all_steps=self.all_steps + (new_step,),
        )

    def record_tool_output(self, output: str) -> 'ExecutionState':
        """
        Record tool output, returns a new state object

        Args:
            output: Tool output content

        Returns:
            New ExecutionState object
        """
        new_step = {
            "type": "tool_output",
            "output": output,
        }
        return replace(
            self,
            response_count=self.response_count + 1,
            tool_output_count=self.tool_output_count + 1,
            all_steps=self.all_steps + (new_step,),
        )

    def record_thinking(self, content: str, display: Any = None) -> 'ExecutionState':

        """
        Record thinking process, returns a new state object

        Args:
            content: Thinking content
            display: User-friendly display data (dict with length, preview, full_content)

        Returns:
            New ExecutionState object
        """
        new_step = {
            "type": "thinking",
            "content": content,
            "display": display,
        }
        if display:
            new_step["display"] = display
        return replace(
            self,
            response_count=self.response_count + 1,
            thinking_count=self.thinking_count + 1,
            all_steps=self.all_steps + (new_step,),
        )

    def accumulate_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str = "",
        total_cost_usd: float | None = None,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
        num_turns: int | None = None,
    ) -> 'ExecutionState':
        """
        Accumulate token usage from an Agent Loop turn.

        Args:
            input_tokens: Input tokens from this turn
            output_tokens: Output tokens from this turn
            model: Model identifier (kept from the latest turn)
            total_cost_usd: SDK-calculated cost for this turn (Claude SDK provides this)
            cache_read_tokens: Prompt-cache read tokens from this turn (accumulated)
            cache_creation_tokens: Prompt-cache write tokens from this turn (accumulated)
            num_turns: Model-call count reported by the framework for this run.
                Latest non-None report wins (it is already a per-run total,
                not a per-event delta, so it must NOT be accumulated).

        Returns:
            New ExecutionState object with updated token counts
        """
        return replace(
            self,
            input_tokens=self.input_tokens + input_tokens,
            output_tokens=self.output_tokens + output_tokens,
            model=model or self.model,
            total_cost_usd=self.total_cost_usd + (total_cost_usd or 0.0),
            cache_read_tokens=self.cache_read_tokens + (cache_read_tokens or 0),
            cache_creation_tokens=self.cache_creation_tokens + (cache_creation_tokens or 0),
            num_turns=num_turns if num_turns is not None else self.num_turns,
        )

    def finalize(self) -> 'ExecutionState':
        """
        Finalize execution, record final output to all_steps

        Returns:
            New ExecutionState object
        """
        if not self.final_output:
            return self

        final_step = {
            "type": "agent_final_output",
            "content": self.final_output,
            "length": len(self.final_output),
        }
        return replace(self, all_steps=self.all_steps + (final_step,))

    def get_all_steps_as_list(self) -> List[Dict[str, Any]]:
        """
        Get all steps as a list (for serialization)

        Returns:
            List of steps
        """
        return list(self.all_steps)
