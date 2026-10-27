import asyncio
import sys

import pytest
from mcp import StdioServerParameters
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from energyops.agents.energy_analyst import ENERGY_ANALYST_SPEC, AnalystRequest, EnergyAnalyst
from energyops.agents.energy_analyst_evals import good_reply
from energyops.agents.fact_sources import DirectFactSheets, McpFactSheets
from energyops.agents.live_eval import LIVE_CASES, evaluate
from energyops.agents.runs import RunRecord, RunStatus
from energyops.agents.sources import DEMO_SITES, synthetic_demo_source
from energyops.facts import build_fact_sheet
from energyops.mcp_servers.energy import create_server
from energyops.models import ModelRequest, ModelResponse
from energyops.models.test_adapter import TestAdapter

SOURCE = synthetic_demo_source()
SHEET = build_fact_sheet(SOURCE(None))
READ_ONLY = ToolAnnotations(read_only_hint=True)


def spec_with(**config) -> object:
    return ENERGY_ANALYST_SPEC.model_copy(
        update={"config": ENERGY_ANALYST_SPEC.config.model_copy(update=config)}
    )


def run_with(facts, site: str | None = None, spec=ENERGY_ANALYST_SPEC, model=None) -> RunRecord:
    answer = good_reply(build_fact_sheet(SOURCE(site if site in DEMO_SITES else None)))
    analyst = EnergyAnalyst(spec, model or TestAdapter(answer), facts)

    async def go():
        return await asyncio.wait_for(
            analyst.run(AnalystRequest(question="Summarise.", site=site)), timeout=60
        )

    return asyncio.run(go())


def via_server(server=None, **kw) -> McpFactSheets:
    return McpFactSheets(kw.pop("spec", ENERGY_ANALYST_SPEC), server or create_server(), **kw)


def tool_calls(record: RunRecord):
    return [s for s in record.steps if s.name == "tool_call"]


# ----------------------------------------------------------------- the normal path


def test_the_spec_declares_the_server_and_tools_it_may_use() -> None:
    config = ENERGY_ANALYST_SPEC.config
    assert config.mcp_servers == ("energyops-energy",)
    assert config.allowed_tools == ("list_sites", "get_fact_sheet")


def test_without_a_site_the_agent_lists_sites_then_gets_the_fact_sheet() -> None:
    record = run_with(via_server())
    assert record.status is RunStatus.SUCCEEDED and record.attempts == 1
    assert [s.name for s in record.steps] == [
        "check_input",
        "tool_call",
        "tool_call",
        "fact_sheet",
        "model_call",
        "validate_output",
    ]
    first, second = tool_calls(record)
    assert first.data == {
        "server": "energyops-energy",
        "tool": "list_sites",
        "arguments": {},
        "is_error": False,
    }
    assert second.data["tool"] == "get_fact_sheet"
    assert second.data["arguments"] == {"site": "demo-hotel"}
    assert record.steps[3].data["via"] == "mcp"


def test_with_a_site_only_the_fact_sheet_tool_is_called() -> None:
    record = run_with(via_server(), site="demo-office")
    assert record.status is RunStatus.SUCCEEDED
    assert [s.data["tool"] for s in tool_calls(record)] == ["get_fact_sheet"]
    assert tool_calls(record)[0].data["arguments"] == {"site": "demo-office"}


@pytest.mark.parametrize("site", [None, "demo-hotel", "demo-office"])
def test_the_model_sees_exactly_the_same_prompt_through_mcp_as_directly(site) -> None:
    seen: dict[str, list[ModelRequest]] = {"mcp": [], "direct": []}

    def spy(label: str) -> TestAdapter:
        class Spy(TestAdapter):
            async def complete(self, request: ModelRequest) -> ModelResponse:
                seen[label].append(request)
                return await super().complete(request)

        return Spy(good_reply(build_fact_sheet(SOURCE(site))))

    by_mcp = run_with(via_server(), site, model=spy("mcp"))
    direct = run_with(DirectFactSheets(SOURCE), site, model=spy("direct"))
    assert by_mcp.status is direct.status is RunStatus.SUCCEEDED
    assert seen["mcp"][0].messages == seen["direct"][0].messages
    mcp_facts = next(s for s in by_mcp.steps if s.name == "fact_sheet").data["facts"]
    direct_facts = next(s for s in direct.steps if s.name == "fact_sheet").data["facts"]
    assert mcp_facts == direct_facts
    assert next(s for s in direct.steps if s.name == "fact_sheet").data["via"] == "direct"
    assert tool_calls(direct) == []


def test_an_invented_figure_is_still_caught_when_the_sheet_came_through_mcp() -> None:
    bad = '{"summary": "We used 999.9 kWh.", "findings": []}'
    record = run_with(via_server(), model=TestAdapter(bad))
    assert (record.status, record.error_kind) == (RunStatus.FAILED, "grounding")


def test_the_scripted_evaluation_cases_behave_the_same_through_mcp() -> None:
    facts = McpFactSheets(ENERGY_ANALYST_SPEC, create_server())
    results = asyncio.run(evaluate(TestAdapter(good_reply(SHEET)), LIVE_CASES[:2], facts))
    assert [r.passed for r in results] == [True, True]
    assert all(len(tool_calls(r.record)) == 2 for r in results)


# ----------------------------------------------------------------- failures from the server


def test_an_unknown_site_through_mcp_is_an_unknown_site_failure() -> None:
    record = run_with(via_server(), site="not-a-site")
    assert (record.status, record.error_kind) == (RunStatus.FAILED, "unknown_site")
    assert record.attempts == 0 and "not-a-site" in (record.error or "")
    assert tool_calls(record)[-1].data["is_error"] is True


def test_unusable_readings_through_mcp_are_an_unusable_data_failure() -> None:
    server = create_server(source=lambda site: [], sites=("empty",))
    record = run_with(via_server(server), site="empty")
    assert (record.status, record.error_kind) == (RunStatus.FAILED, "unusable_data")


# ----------------------------------------------------------------- permissions are enforced


def test_a_tool_outside_the_allow_list_is_refused_before_anything_is_called() -> None:
    narrow = spec_with(allowed_tools=("get_fact_sheet",))
    record = run_with(via_server(spec=narrow), spec=narrow)  # no site, so it would need list_sites
    assert (record.status, record.error_kind) == (RunStatus.FAILED, "tool_error")
    assert "list_sites" in (record.error or "") and tool_calls(record) == []
    assert (
        run_with(via_server(spec=narrow), "demo-hotel", spec=narrow).status is RunStatus.SUCCEEDED
    )


def test_a_server_outside_the_spec_is_refused() -> None:
    none_allowed = spec_with(mcp_servers=())
    record = run_with(via_server(spec=none_allowed), "demo-hotel", spec=none_allowed)
    assert record.error_kind == "tool_error" and "may not use the MCP server" in (
        record.error or ""
    )
    assert tool_calls(record) == []


def fake_server(*, annotations=READ_ONLY, offer=True, body=None) -> MCPServer:
    server = MCPServer("energyops-energy")
    if offer:

        @server.tool(annotations=annotations)
        async def get_fact_sheet(site: str) -> dict[str, int]:
            if body is not None:
                await body()
            return {"nope": 1}

    return server


def test_a_tool_that_is_not_marked_read_only_is_refused() -> None:
    record = run_with(via_server(fake_server(annotations=None)), "demo-hotel")
    assert record.error_kind == "tool_error" and "not marked read only" in (record.error or "")
    assert tool_calls(record) == []


def test_a_tool_marked_as_writing_is_refused() -> None:
    writes = ToolAnnotations(read_only_hint=False, destructive_hint=True)
    record = run_with(via_server(fake_server(annotations=writes)), "demo-hotel")
    assert record.error_kind == "tool_error" and "not marked read only" in (record.error or "")


def test_a_server_that_lacks_the_tool_is_reported() -> None:
    record = run_with(via_server(fake_server(offer=False)), "demo-hotel")
    assert record.error_kind == "tool_error" and "does not offer" in (record.error or "")


def test_a_fact_sheet_in_the_wrong_shape_is_reported_not_trusted() -> None:
    record = run_with(via_server(fake_server()), "demo-hotel")
    assert record.error_kind == "tool_error" and "unexpected shape" in (record.error or "")
    assert record.attempts == 0


# ----------------------------------------------------------------- a broken or slow server


def test_a_server_that_dies_at_start_is_a_failed_run_not_a_crash() -> None:
    dead = StdioServerParameters(command=sys.executable, args=["-c", "import sys; sys.exit(3)"])
    record = run_with(McpFactSheets(ENERGY_ANALYST_SPEC, dead), "demo-hotel")
    assert (record.status, record.error_kind) == (RunStatus.FAILED, "tool_error")
    assert "energy tool server failed" in (record.error or "")


def test_a_slow_server_times_out() -> None:
    async def slow() -> None:
        await asyncio.sleep(10)

    quick = spec_with(timeout_seconds=0.5)
    record = run_with(via_server(fake_server(body=slow), spec=quick), "demo-hotel", spec=quick)
    assert (record.status, record.error_kind) == (RunStatus.FAILED, "tool_timeout")


# ----------------------------------------------------------------- a real subprocess, end to end


def test_the_agent_works_end_to_end_with_a_real_server_subprocess() -> None:
    record = run_with(McpFactSheets(ENERGY_ANALYST_SPEC), "demo-hotel")
    assert record.status is RunStatus.SUCCEEDED
    assert [s.data["tool"] for s in tool_calls(record)] == ["get_fact_sheet"]
    figures = next(s for s in record.steps if s.name == "fact_sheet").data["facts"]
    assert figures[0]["display"] == build_fact_sheet(SOURCE("demo-hotel")).facts[0].display
