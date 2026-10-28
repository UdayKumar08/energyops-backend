"""How an agent gets its fact sheet.

Two ways, with the same result. `DirectFactSheets` computes it in Python in the same process.
`McpFactSheets` asks the read-only energy MCP server for it. Either way the numbers come from
our own calculation functions and the agent checks every figure it writes against the result.

The MCP path is where an agent's permissions become real. The spec says which servers and tools
the agent may use, and this code refuses anything else. It also refuses any tool that does not
declare itself read only, so an agent in this codebase cannot be pointed at something that
changes data by accident. Python decides which tools to call and in what order. The model never
chooses a tool.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Sequence
from typing import Any, Protocol

from mcp import Client, StdioServerParameters
from mcp.server import MCPServer
from pydantic import ValidationError

from energyops.agents.runs import TraceRecorder
from energyops.agents.sources import ReadingsSource
from energyops.agents.spec import AgentSpec
from energyops.calculations import EnergyDataError
from energyops.facts import FactSheet, build_fact_sheet
from energyops.mcp_servers.energy import SERVER_NAME
from energyops.mcp_servers.schema import FactSheetModel

# The server marks failures it expected with one of these words and a colon, so the client can
# tell "no such site" from "readings cannot be used" without guessing from the sentence.
UNKNOWN_SITE = "unknown_site"
UNUSABLE_DATA = "unusable_data"


class ToolAccessError(Exception):
    """The agent may not use, or could not reach, the tool it needs."""


class FactSheetProvider(Protocol):
    via: str

    async def __call__(self, site: str | None, trace: TraceRecorder) -> FactSheet: ...


class DirectFactSheets:
    via = "direct"

    def __init__(self, source: ReadingsSource) -> None:
        self._source = source

    async def __call__(self, site: str | None, trace: TraceRecorder) -> FactSheet:
        return build_fact_sheet(self._source(site))


McpTarget = MCPServer | StdioServerParameters


def energy_server_command() -> StdioServerParameters:
    """Start our own energy server as a subprocess, with the interpreter running this code."""
    return StdioServerParameters(
        command=sys.executable, args=["-m", "energyops.mcp_servers.energy"]
    )


class McpToolSource:
    """What every MCP-backed fact source shares: the permission checks, the read-only check on
    each tool, the site lookup and the traced tool calls."""

    via = "mcp"

    def __init__(
        self, spec: AgentSpec, target: McpTarget | None = None, server_name: str = SERVER_NAME
    ) -> None:
        self._servers = frozenset(spec.config.mcp_servers)
        self._allowed = frozenset(spec.config.allowed_tools)
        self._target = target if target is not None else energy_server_command()
        self._server_name = server_name

    def _guard(self, site: str | None, tools: Sequence[str]) -> list[str]:
        """Refuse a server or tool the spec does not name. Returns every tool that will be used."""
        if self._server_name not in self._servers:
            raise ToolAccessError(f"this agent may not use the MCP server '{self._server_name}'")
        needed = [*tools] if site else ["list_sites", *tools]
        not_allowed = [tool for tool in needed if tool not in self._allowed]
        if not_allowed:
            raise ToolAccessError(f"this agent may not use the tools: {', '.join(not_allowed)}")
        return needed

    @staticmethod
    def _raise_failure(kind: str, payload: Any) -> None:
        """Turn a failed outcome into the exception the agent understands. Called outside the
        client's context so the SDK cannot wrap it in an exception group."""
        if kind == UNKNOWN_SITE:
            raise LookupError(payload)
        if kind == UNUSABLE_DATA:
            raise EnergyDataError(payload)
        raise ToolAccessError(payload)

    async def _fetch(
        self,
        site: str | None,
        needed: list[str],
        trace: TraceRecorder,
        tools: Sequence[str] = ("get_fact_sheet",),
    ) -> tuple[str, Any]:
        """Call each of `tools` for the site. On success the payload maps tool name to result."""
        try:
            async with Client(self._target) as client:
                offered = {tool.name: tool for tool in (await client.list_tools()).tools}
                for name in needed:
                    tool = offered.get(name)
                    if tool is None:
                        return "error", f"the server does not offer the tool '{name}'"
                    hints = tool.annotations
                    if hints is None or hints.read_only_hint is not True:
                        return (
                            "error",
                            f"the tool '{name}' is not marked read only, so it is refused",
                        )

                chosen = site
                if chosen is None:
                    kind, payload = await self._call(client, trace, "list_sites", {})
                    if kind != "ok":
                        return kind, payload
                    chosen = payload.get("default_site") if isinstance(payload, dict) else None
                    if not isinstance(chosen, str) or not chosen:
                        return "error", "list_sites did not name a default site"
                results: dict[str, Any] = {}
                for name in tools:
                    kind, payload = await self._call(client, trace, name, {"site": chosen})
                    if kind != "ok":
                        return kind, payload
                    results[name] = payload
                return "ok", results
        except TimeoutError:
            raise
        except Exception as exc:  # the SDK, the subprocess or the pipe failed
            return (
                "error",
                f"the energy tool server failed ({type(exc).__name__}: {str(exc)[:200]})",
            )

    async def _call(
        self, client: Client, trace: TraceRecorder, tool: str, arguments: dict[str, Any]
    ) -> tuple[str, Any]:
        started = time.perf_counter()
        result = await client.call_tool(tool, arguments)
        trace.step(
            "tool_call",
            started,
            server=self._server_name,
            tool=tool,
            arguments=arguments,
            is_error=result.is_error,
        )
        if result.is_error:
            text = (
                result.content[0].text
                if result.content and hasattr(result.content[0], "text")
                else ""
            )
            code, _, message = text.removeprefix("Error executing tool " + tool + ": ").partition(
                ": "
            )
            if code in (UNKNOWN_SITE, UNUSABLE_DATA) and message:
                return code, message
            return "error", f"the tool '{tool}' failed: {text[:200]}"
        if result.structured_content is None:
            return "error", f"the tool '{tool}' returned no structured result"
        return "ok", result.structured_content


class McpFactSheets(McpToolSource):
    async def __call__(self, site: str | None, trace: TraceRecorder) -> FactSheet:
        needed = self._guard(site, ["get_fact_sheet"])
        kind, payload = await self._fetch(site, needed, trace)
        if kind != "ok":
            self._raise_failure(kind, payload)
        try:
            return FactSheetModel.model_validate(payload["get_fact_sheet"]).to_fact_sheet()
        except (ValidationError, ValueError):
            raise ToolAccessError("the tool returned a fact sheet in an unexpected shape") from None
