"""Host-side custom tools.

Agents declare these tools in their definitions; when an agent calls one, the session
emits `agent.custom_tool_use` and this process (running on the user's own machine)
executes it and sends `user.custom_tool_result` back. The sandbox never sees local
credentials or the local filesystem.
"""

from kronos.tools.registry import ToolContext, ToolError, ToolRegistry, build_default_registry

__all__ = ["ToolContext", "ToolError", "ToolRegistry", "build_default_registry"]
