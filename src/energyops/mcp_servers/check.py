"""Start the energy MCP server as a subprocess, use it like a real client would, and print what
happened:

    uv run python -m energyops.mcp_servers.check

It lists the tools and whether each is read only, then asks for each site's fact sheet and prints
the first fact. Nothing here calls a model and no key is needed.
"""

from __future__ import annotations

import asyncio
import sys

from mcp import Client, StdioServerParameters


async def check() -> int:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "energyops.mcp_servers.energy"]
    )
    async with Client(params) as client:
        info = client.server_info
        print(f"server: {info.name if info else 'unknown'}")
        for tool in (await client.list_tools()).tools:
            read_only = tool.annotations.read_only_hint if tool.annotations else None
            print(f"tool: {tool.name} (read only: {read_only})")
        listed = await client.call_tool("list_sites", {})
        sites = (listed.structured_content or {}).get("sites", [])
        if listed.is_error or not sites:
            print("FAILED: list_sites gave no sites")
            return 1
        for site in sites:
            result = await client.call_tool("get_fact_sheet", {"site": site})
            facts = (result.structured_content or {}).get("facts", [])
            if result.is_error or not facts:
                print(f"FAILED: no fact sheet for {site}")
                return 1
            first = facts[0]
            print(f"{site}: {first['description']}: {first['display']} ({len(facts)} facts)")
    return 0


def main() -> None:
    try:
        code = asyncio.run(asyncio.wait_for(check(), timeout=60))
    except TimeoutError:
        print("FAILED: the server did not answer within 60 seconds")
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
