from narranexus.sdk import Contribution, McpServerSpec, ToolSpec


class _Provider:
    def list_tools(self):
        return (
            # Reachable through tool_search by default; always_visible=True puts it in the up-front list.
            ToolSpec("__PLUGIN_PKG___echo", "Echo a message back", server="__PLUGIN_PKG__", input_schema={"type": "object", "properties": {"text": {"type": "string"}}}),
        )


TOOLS = (Contribution("tools", _Provider),)
# The MCP server that serves the tools (stdio: the host starts it; url transports are reached directly).
MCP_SERVERS = (Contribution("__PLUGIN_PKG__", lambda: McpServerSpec("__PLUGIN_PKG__", "stdio", command="python", args=("-m", "nxplugins.__PLUGIN_PKG__.server"))),)


def activate(ctx):
    ctx.log.info("tools declared")
