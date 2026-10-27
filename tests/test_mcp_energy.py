import asyncio
import sys

import pytest
from mcp import Client, StdioServerParameters

from energyops.agents.sources import DEMO_SITES, synthetic_demo_source
from energyops.facts import build_fact_sheet
from energyops.mcp_servers.energy import SERVER_NAME, create_server
from energyops.mcp_servers.schema import FactSheetModel

SOURCE = synthetic_demo_source()


def with_client(action):
    """Run `action(client)` against a fresh in-process server and return its result."""

    async def go():
        async with Client(create_server()) as client:
            return await action(client)

    return asyncio.run(go())


def call(name: str, arguments: dict):
    return with_client(lambda client: client.call_tool(name, arguments))


# --------------------------------------------------------------- what the server offers


def test_exactly_two_tools_are_offered_and_all_are_marked_read_only() -> None:
    tools = with_client(lambda c: c.list_tools()).tools
    assert sorted(t.name for t in tools) == ["get_fact_sheet", "list_sites"]
    for tool in tools:
        hints = tool.annotations
        assert hints is not None
        assert hints.read_only_hint is True and hints.destructive_hint is False
        assert hints.open_world_hint is False and hints.idempotent_hint is True


def test_no_tool_accepts_a_path_a_url_or_anything_but_a_site_id() -> None:
    tools = {t.name: t for t in with_client(lambda c: c.list_tools()).tools}
    assert tools["list_sites"].input_schema.get("properties", {}) == {}
    schema = tools["get_fact_sheet"].input_schema
    assert list(schema["properties"]) == ["site"]
    assert schema["required"] == ["site"] and schema["properties"]["site"]["maxLength"] == 64


def test_the_server_names_itself_and_explains_how_to_use_it() -> None:
    async def info(client):
        return client.server_info.name, client.instructions

    name, instructions = with_client(info)
    assert name == SERVER_NAME and "list_sites" in (instructions or "")


# --------------------------------------------------------------- the numbers come from Python


def test_list_sites_returns_the_demo_sites() -> None:
    result = call("list_sites", {})
    assert not result.is_error
    assert result.structured_content == {"sites": list(DEMO_SITES), "default_site": DEMO_SITES[0]}


@pytest.mark.parametrize("site", DEMO_SITES)
def test_the_fact_sheet_is_exactly_what_our_python_functions_compute(site: str) -> None:
    result = call("get_fact_sheet", {"site": site})
    assert not result.is_error
    expected = build_fact_sheet(SOURCE(site))
    got = FactSheetModel.model_validate(result.structured_content)
    assert got.meter_id == expected.meter_id
    assert [(f.id, f.key, f.value, f.unit, f.display) for f in got.facts] == [
        (f.id, f.key, f.value, f.unit, f.display) for f in expected.facts
    ]
    assert got.prompt_text == expected.to_prompt_text()
    assert got.warnings == list(expected.warnings)


@pytest.mark.parametrize("site", DEMO_SITES)
def test_a_fact_sheet_survives_the_trip_and_rebuilds_exactly(site: str) -> None:
    original = build_fact_sheet(SOURCE(site))
    rebuilt = FactSheetModel.from_fact_sheet(original).to_fact_sheet()
    assert rebuilt == original
    assert rebuilt.to_prompt_text() == original.to_prompt_text()
    peak = next(f for f in rebuilt.facts if f.key == "peak_demand")
    assert peak.when is not None and peak.when.isoformat().count("T") == 1


def test_the_two_demo_sites_have_different_figures() -> None:
    hotel = call("get_fact_sheet", {"site": "demo-hotel"}).structured_content
    office = call("get_fact_sheet", {"site": "demo-office"}).structured_content
    assert hotel is not None and office is not None
    assert hotel["facts"][0]["display"] != office["facts"][0]["display"]


# --------------------------------------------------------------- bad calls fail loudly


def test_an_unknown_site_is_an_error_that_tells_the_model_what_to_do() -> None:
    result = call("get_fact_sheet", {"site": "not-a-site"})
    assert result.is_error
    assert (
        "Unknown site 'not-a-site'" in result.content[0].text
        and "list_sites" in result.content[0].text
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"path": "/etc/passwd"},
        {"site_id": "demo-office"},
        {"site": ""},
        {"site": "x" * 65},
        {"site": 5},
    ],
)
def test_wrong_arguments_never_quietly_return_some_site(arguments: dict) -> None:
    result = call("get_fact_sheet", arguments)
    assert result.is_error
    assert result.structured_content is None or "facts" not in result.structured_content


def test_unusable_readings_are_reported_not_guessed() -> None:
    async def go():
        server = create_server(source=lambda site: [], sites=("empty",))
        async with Client(server) as client:
            return await client.call_tool("get_fact_sheet", {"site": "empty"})

    result = asyncio.run(go())
    assert result.is_error and "cannot be used" in result.content[0].text


def test_an_unknown_tool_is_an_error() -> None:
    assert call("delete_everything", {}).is_error


# --------------------------------------------------------------- over a real stdio connection


def test_a_real_client_can_start_the_server_as_a_subprocess_and_use_it() -> None:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "energyops.mcp_servers.energy"]
    )

    async def go():
        async with Client(params) as client:
            tools = await client.list_tools()
            sheet = await client.call_tool("get_fact_sheet", {"site": "demo-hotel"})
            bad = await client.call_tool("get_fact_sheet", {"site": "nope"})
            return client.server_info.name, [t.name for t in tools.tools], sheet, bad

    name, tools, sheet, bad = asyncio.run(asyncio.wait_for(go(), timeout=60))
    assert name == SERVER_NAME and sorted(tools) == ["get_fact_sheet", "list_sites"]
    expected = build_fact_sheet(SOURCE("demo-hotel")).facts[0].display
    assert sheet.structured_content["facts"][0]["display"] == expected
    assert bad.is_error


def test_the_check_command_reports_tools_and_figures(capsys) -> None:
    from energyops.mcp_servers import check

    assert asyncio.run(asyncio.wait_for(check.check(), timeout=60)) == 0
    out = capsys.readouterr().out
    assert "server: energyops-energy" in out
    assert "tool: get_fact_sheet (read only: True)" in out
    expected = build_fact_sheet(SOURCE("demo-office")).facts[0].display
    assert f"demo-office: Total energy over all 1344 readings: {expected} (10 facts)" in out
