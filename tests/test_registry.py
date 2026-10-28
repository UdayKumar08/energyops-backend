import pytest

from energyops.agents import registry
from energyops.agents.registry import AGENT_ENTRIES, AGENTS, MAX_AGENTS, InvalidRunInput, RunInputs
from energyops.agents.spec import load_prompt, unimplemented_guardrails


def test_there_are_never_more_than_five_agents_and_ids_and_names_are_unique() -> None:
    assert 1 <= len(AGENT_ENTRIES) <= MAX_AGENTS == 5
    assert len(AGENTS) == len(AGENT_ENTRIES)
    assert len({e.spec.name for e in AGENT_ENTRIES}) == len(AGENT_ENTRIES)


@pytest.mark.parametrize("entry", AGENT_ENTRIES, ids=[e.id for e in AGENT_ENTRIES])
def test_every_agent_is_a_draft_with_a_loadable_prompt_and_implemented_guardrails(entry) -> None:
    assert entry.spec.status.value == "draft"
    assert load_prompt(entry.spec).text
    assert unimplemented_guardrails(entry.spec) == []
    assert entry.input_kind in ("question", "audience", "anomaly", "none")
    # Events are listed per site instead, and a savings run needs nothing but a site.
    assert entry.examples or entry.input_kind in ("anomaly", "none")


@pytest.mark.parametrize("entry", AGENT_ENTRIES, ids=[e.id for e in AGENT_ENTRIES])
def test_no_agent_can_reach_a_tool_outside_the_read_only_energy_server(entry) -> None:
    config = entry.spec.config
    assert set(config.mcp_servers) <= {"energyops-energy"}
    assert set(config.allowed_tools) <= {
        "list_sites",
        "get_fact_sheet",
        "get_anomalies",
        "get_savings_facts",
    }


def test_each_agent_builds_the_request_it_takes_and_refuses_the_other_kind() -> None:
    analyst, writer = AGENTS["energy-analyst"], AGENTS["report-writer"]
    assert analyst.make_request(RunInputs(question="Hi", site="demo-office")).site == "demo-office"
    assert writer.make_request(RunInputs()).model_dump() == {"site": None, "audience": "manager"}
    with pytest.raises(InvalidRunInput):
        analyst.make_request(RunInputs())
    with pytest.raises(InvalidRunInput):
        writer.make_request(RunInputs(question="Hi"))


def test_the_ceiling_is_five_not_a_target() -> None:
    assert registry.MAX_AGENTS == 5


def test_the_investigator_takes_an_event_id_and_refuses_the_other_kinds_of_input() -> None:
    investigator = AGENTS["anomaly-investigator"]
    request = investigator.make_request(RunInputs(site="demo-clinic", anomaly_id="A2"))
    assert request.model_dump() == {"site": "demo-clinic", "anomaly_id": "A2"}
    for bad in (
        RunInputs(),
        RunInputs(anomaly_id="nope"),
        RunInputs(anomaly_id="A1", question="why?"),
        RunInputs(anomaly_id="A1", audience="manager"),
    ):
        with pytest.raises(InvalidRunInput):
            investigator.make_request(bad)


def test_the_other_agents_refuse_an_event_id_and_say_what_they_do_take() -> None:
    with pytest.raises(InvalidRunInput, match="takes question, not anomaly id"):
        AGENTS["energy-analyst"].make_request(RunInputs(question="Hi", anomaly_id="A1"))
    with pytest.raises(InvalidRunInput, match="takes audience, not anomaly id"):
        AGENTS["report-writer"].make_request(RunInputs(anomaly_id="A1"))


def test_the_savings_advisor_takes_only_a_site() -> None:
    advisor = AGENTS["savings-advisor"]
    assert advisor.make_request(RunInputs(site="demo-hotel")).model_dump() == {"site": "demo-hotel"}
    assert advisor.make_request(RunInputs()).model_dump() == {"site": None}
    for bad in (
        RunInputs(question="Hi"),
        RunInputs(audience="manager"),
        RunInputs(anomaly_id="A1"),
    ):
        with pytest.raises(InvalidRunInput, match="takes only a site"):
            advisor.make_request(bad)
