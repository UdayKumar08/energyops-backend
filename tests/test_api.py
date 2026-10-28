import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from energyops.agents.runs import RunRecord, RunStatus
from energyops.api import __main__ as api_main
from energyops.api.app import ApiSettings, ApiStartupRefused, create_app
from energyops.api.store import RunStore
from energyops.config import Environment
from energyops.mcp_servers.energy import create_server
from energyops.models.credentials import ProviderKeys
from energyops.models.test_adapter import SimulatedProviderFailure, TestAdapter

NO_KEYS = ProviderKeys(gemini_api_key=None, nebius_api_key=None)
SECRET = "sk-this-must-never-appear-in-any-response"
BOTH_KEYS = ProviderKeys(gemini_api_key=SecretStr(SECRET), nebius_api_key=SecretStr(SECRET + "2"))


def client_for(**kw) -> TestClient:
    kw.setdefault("keys", NO_KEYS)
    # An in-process MCP server keeps these tests fast. One test below uses a real subprocess.
    kw.setdefault("mcp_target", create_server())
    # The API only answers to localhost host names, so the test client must use one.
    return TestClient(
        create_app(ApiSettings(environment=Environment.LOCAL), **kw), "http://127.0.0.1"
    )


def run(client: TestClient, **body):
    return client.post("/agents/energy-analyst/runs", json={"question": "Summarise.", **body})


# --------------------------------------------------------------- local only


@pytest.mark.parametrize("environment", [Environment.DEV, Environment.NONPROD, Environment.PROD])
def test_the_app_refuses_to_be_created_outside_the_local_environment(environment) -> None:
    with pytest.raises(ApiStartupRefused, match=environment.value):
        create_app(ApiSettings(environment=environment))


def test_the_command_refuses_to_start_outside_local(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("ENERGYOPS_ENVIRONMENT", "prod")
    monkeypatch.setattr("sys.argv", ["energyops-api"])
    started = []
    monkeypatch.setattr(api_main.uvicorn, "run", lambda *a, **k: started.append((a, k)))
    with pytest.raises(SystemExit, match="Refusing to start"):
        api_main.main()
    assert started == []


def test_the_command_listens_on_loopback_only(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ENERGYOPS_ENVIRONMENT", raising=False)
    monkeypatch.setattr("sys.argv", ["energyops-api", "--port", "8123"])
    started = []
    monkeypatch.setattr(api_main.uvicorn, "run", lambda *a, **k: started.append(k))
    api_main.main()
    assert started == [{"host": "127.0.0.1", "port": 8123}]


def test_requests_for_other_host_names_are_refused() -> None:
    app = create_app(ApiSettings(environment=Environment.LOCAL), keys=NO_KEYS)
    assert TestClient(app, "http://127.0.0.1").get("/health").status_code == 200
    assert TestClient(app, "http://localhost").get("/health").status_code == 200
    assert TestClient(app, "http://evil.example").get("/health").status_code == 400


def test_health_reports_the_environment() -> None:
    assert client_for().get("/health").json() == {"status": "ok", "environment": "local"}


# --------------------------------------------------------------- agents and providers


def test_agents_are_listed_with_registry_style_fields() -> None:
    agents = client_for().get("/agents").json()
    assert [a["id"] for a in agents] == [
        "energy-analyst",
        "report-writer",
        "anomaly-investigator",
    ]
    agent = agents[0]
    assert agent["status"] == "draft" and agent["prompt_version"] == "v3"
    assert "peak-demand" in agent["capabilities"]
    assert agent["input_kind"] == "question" and agent["examples"][0]["label"] == "Summary"
    writer = agents[1]
    assert writer["input_kind"] == "audience" and writer["prompt_version"] == "v1"
    assert [e["value"] for e in writer["examples"]] == ["manager", "technical"]


def test_agent_detail_shows_spec_prompt_and_unimplemented_guardrails() -> None:
    detail = client_for().get("/agents/energy-analyst").json()
    assert detail["spec"]["config"]["max_iterations"] == 2
    assert detail["prompt"]["version"] == "v3" and len(detail["prompt"]["sha256"]) == 64
    assert "fact sheet" in detail["prompt"]["text"]
    assert detail["guardrails_not_implemented"] == []


def test_an_unknown_agent_is_a_404() -> None:
    client = client_for()
    assert client.get("/agents/nope").status_code == 404
    assert client.post("/agents/nope/runs", json={"question": "x"}).status_code == 404


def test_providers_say_which_keys_are_set_and_never_show_them() -> None:
    none = {p["id"]: p for p in client_for().get("/providers").json()}
    assert none["test"]["available"] is True
    assert none["gemini"]["available"] is False and "GEMINI_API_KEY" in none["gemini"]["note"]
    both = client_for(keys=BOTH_KEYS).get("/providers")
    assert all(p["available"] for p in both.json())
    assert SECRET not in both.text


# --------------------------------------------------------------- running


def test_a_run_with_the_test_adapter_succeeds_and_is_labelled() -> None:
    response = run(client_for())
    assert response.status_code == 200
    record = response.json()
    assert record["status"] == "succeeded" and record["attempts"] == 1
    assert record["model_provider"] == "test-adapter"
    assert [s["name"] for s in record["steps"]][-1] == "validate_output"
    assert record["output"]["findings"]


def test_a_run_is_stored_and_can_be_listed_and_read_back() -> None:
    client = client_for()
    first = run(client, question="First question").json()
    second = run(client, question="Second question").json()
    listed = client.get("/runs").json()
    assert [r["run_id"] for r in listed] == [second["run_id"], first["run_id"]]
    assert listed[0]["question"] == "Second question" and listed[0]["status"] == "succeeded"
    assert client.get(f"/runs/{first['run_id']}").json()["run_id"] == first["run_id"]
    assert len(client.get("/runs", params={"limit": 1}).json()) == 1
    assert client.get("/runs", params={"agent": "someone else"}).json() == []


def test_unknown_runs_and_bad_limits_are_rejected() -> None:
    client = client_for()
    assert client.get("/runs/not-a-run").status_code == 404
    assert client.get("/runs", params={"limit": 0}).status_code == 422
    assert client.get("/runs", params={"limit": 201}).status_code == 422


def test_a_run_that_fails_its_checks_is_still_a_recorded_200() -> None:
    record = run(client_for(), site="not-a-site").json()
    assert record["status"] == "failed" and record["error_kind"] == "unknown_site"


def test_a_provider_failure_is_a_failed_record_not_a_server_error() -> None:
    def failing(provider, model, keys, reply):
        return TestAdapter(fail_with=SimulatedProviderFailure(429, "slow down"))

    response = run(client_for(client_factory=failing), provider="gemini")
    assert response.status_code == 200
    assert response.json()["error_kind"] == "model_error:ModelRateLimitError"


def test_a_missing_key_is_a_503_that_names_the_setting() -> None:
    response = run(client_for(), provider="nebius")
    assert response.status_code == 503 and "NEBIUS_API_KEY" in response.json()["detail"]


def test_the_chosen_provider_and_model_reach_the_client_factory() -> None:
    seen = []

    def spy(provider, model, keys, reply):
        seen.append((provider, model))
        return TestAdapter(reply)

    client = client_for(keys=BOTH_KEYS, client_factory=spy)
    response = run(client, provider="gemini", model="some-exact-model")
    assert seen == [("gemini", "some-exact-model")]
    assert SECRET not in response.text and SECRET not in client.get("/runs").text


@pytest.mark.parametrize(
    "body",
    [
        {"question": ""},
        {"question": "x" * 501},
        {"question": "ok", "provider": "openai"},
        {"question": "ok", "extra_field": 1},
        {"question": "ok", "model": ""},
        {},
    ],
)
def test_bad_run_requests_are_rejected_before_anything_runs(body) -> None:
    client = client_for()
    assert client.post("/agents/energy-analyst/runs", json=body).status_code == 422
    assert client.get("/runs").json() == []


# --------------------------------------------------------------- the store


def make_record(run_id: str, agent: str = "a") -> RunRecord:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    return RunRecord(
        run_id=run_id,
        agent=agent,
        prompt_version="v1",
        prompt_sha256="0" * 64,
        model_provider="p",
        model_name="m",
        status=RunStatus.SUCCEEDED,
        started_at=now,
        finished_at=now,
        input={"question": "q"},
    )


def test_the_store_drops_the_oldest_records_past_its_limit() -> None:
    store = RunStore(limit=2)
    for run_id in ("r1", "r2", "r3"):
        store.add(make_record(run_id))
    assert store.get("r1") is None
    assert [r.run_id for r in store.newest_first()] == ["r3", "r2"]


def test_the_store_filters_by_agent_and_validates_its_limit() -> None:
    store = RunStore()
    store.add(make_record("r1", "a"))
    store.add(make_record("r2", "b"))
    assert [r.run_id for r in store.newest_first(agent="a")] == ["r1"]
    with pytest.raises(ValueError, match="at least 1"):
        RunStore(limit=0)


# --------------------------------------------------------------- how the agent gets its data


def test_runs_fetch_the_fact_sheet_through_mcp_by_default() -> None:
    record = run(client_for()).json()
    tools = [s["data"]["tool"] for s in record["steps"] if s["name"] == "tool_call"]
    assert tools == ["list_sites", "get_fact_sheet"]
    assert next(s for s in record["steps"] if s["name"] == "fact_sheet")["data"]["via"] == "mcp"


def test_direct_data_access_makes_no_tool_calls() -> None:
    record = run(client_for(), data_access="direct").json()
    assert record["status"] == "succeeded"
    assert [s for s in record["steps"] if s["name"] == "tool_call"] == []
    assert next(s for s in record["steps"] if s["name"] == "fact_sheet")["data"]["via"] == "direct"


def test_an_unknown_data_access_mode_is_rejected() -> None:
    assert run(client_for(), data_access="ftp").status_code == 422


def test_a_custom_data_source_reaches_the_mcp_path_too() -> None:
    from energyops.agents.sources import synthetic_demo_source

    custom = synthetic_demo_source(seed=99, days=7)
    app = create_app(ApiSettings(environment=Environment.LOCAL), keys=NO_KEYS, source=custom)
    record = run(TestClient(app, "http://127.0.0.1"), site="demo-hotel").json()
    assert record["status"] == "succeeded"
    figures = next(s for s in record["steps"] if s["name"] == "fact_sheet")["data"]["facts"]
    assert "672 readings" in figures[0]["description"]  # 7 days, not the default 14


def test_the_default_server_is_a_real_subprocess_and_works_through_the_api() -> None:
    app = create_app(ApiSettings(environment=Environment.LOCAL), keys=NO_KEYS)  # no mcp_target
    record = run(TestClient(app, "http://127.0.0.1"), site="demo-office").json()
    assert record["status"] == "succeeded"
    calls = [s for s in record["steps"] if s["name"] == "tool_call"]
    assert [c["data"]["tool"] for c in calls] == ["get_fact_sheet"]


# --------------------------------------------------------------- the second agent


def run_report(client: TestClient, **body):
    return client.post("/agents/report-writer/runs", json=body)


def test_the_report_writer_runs_with_the_test_adapter_and_is_labelled() -> None:
    response = run_report(client_for(), audience="technical")
    assert response.status_code == 200
    record = response.json()
    assert record["status"] == "succeeded" and record["model_provider"] == "test-adapter"
    assert record["agent"] == "EnergyOps Report Writer"
    assert record["input"] == {"site": None, "audience": "technical"}
    assert [s["heading"] for s in record["output"]["sections"]] == ["Consumption", "Demand"]
    assert "fact_sheet" in [s["name"] for s in record["steps"]]


def test_the_report_writer_defaults_to_a_manager_audience_and_works_on_either_data_path() -> None:
    client = client_for()
    for access in ("mcp", "direct"):
        record = run_report(client, data_access=access, site="demo-office").json()
        assert record["status"] == "succeeded" and record["input"]["audience"] == "manager"
        via = next(s for s in record["steps"] if s["name"] == "fact_sheet")["data"]["via"]
        assert via == access


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("report-writer", {"question": "Summarise."}),
        ("report-writer", {"audience": "the press"}),
        ("energy-analyst", {"audience": "manager"}),
        ("energy-analyst", {}),
    ],
)
def test_input_that_does_not_fit_the_agent_is_a_422_and_nothing_runs(path, body) -> None:
    client = client_for()
    assert client.post(f"/agents/{path}/runs", json=body).status_code == 422
    assert client.get("/runs").json() == []


def test_runs_of_both_agents_are_listed_with_something_readable_and_filterable() -> None:
    client = client_for()
    run(client, question="Summarise.")
    run_report(client, audience="technical")
    listed = client.get("/runs").json()
    assert [r["question"] for r in listed] == ["Report for technical", "Summarise."]
    only = client.get("/runs", params={"agent": "EnergyOps Report Writer"}).json()
    assert [r["agent"] for r in only] == ["EnergyOps Report Writer"]


def test_report_writer_detail_shows_its_own_prompt() -> None:
    detail = client_for().get("/agents/report-writer").json()
    assert detail["prompt"]["version"] == "v1" and "Data quality" in detail["prompt"]["text"]
    assert detail["spec"]["config"]["allowed_tools"] == ["list_sites", "get_fact_sheet"]


# --------------------------------------------------------------- the third agent


def run_investigation(client: TestClient, **body):
    return client.post("/agents/anomaly-investigator/runs", json=body)


def test_the_anomalies_endpoint_lists_the_events_for_a_site() -> None:
    client = client_for()
    clinic = client.get("/anomalies", params={"site": "demo-clinic"}).json()
    assert [(a["id"], a["kind"]) for a in clinic["anomalies"]] == [("A1", "spike"), ("A2", "drop")]
    assert client.get("/anomalies", params={"site": "demo-hotel"}).json()["anomalies"] == []
    assert client.get("/anomalies").json()["meter_id"] == "demo-hotel"  # the default site
    assert client.get("/anomalies", params={"site": "nowhere"}).status_code == 404
    assert client.get("/anomalies", params={"site": "x" * 65}).status_code == 422


def test_the_investigator_runs_with_the_test_adapter_through_mcp_and_directly() -> None:
    client = client_for()
    for access in ("mcp", "direct"):
        record = run_investigation(
            client, site="demo-clinic", anomaly_id="A2", data_access=access
        ).json()
        assert record["status"] == "succeeded" and record["model_provider"] == "test-adapter"
        assert record["agent"] == "EnergyOps Anomaly Investigator"
        assert record["output"]["hypotheses"][0]["confidence"] == "low"
        via = next(s for s in record["steps"] if s["name"] == "fact_sheet")["data"]["via"]
        assert via == access
    assert [s["name"] for s in record["steps"] if s["name"] == "tool_call"] == []  # direct


def test_an_unknown_event_is_a_recorded_failure_not_a_server_error() -> None:
    client = client_for()
    record = run_investigation(client, site="demo-clinic", anomaly_id="A9").json()
    assert record["status"] == "failed" and record["error_kind"] == "unknown_anomaly"
    assert "A1, A2" in record["error"] and record["attempts"] == 0


@pytest.mark.parametrize(
    "body",
    [{}, {"anomaly_id": "x"}, {"anomaly_id": "A1", "question": "why?"}, {"anomaly_id": "A1" * 9}],
)
def test_input_that_does_not_fit_the_investigator_is_a_422_and_nothing_runs(body) -> None:
    client = client_for()
    assert run_investigation(client, **body).status_code == 422
    assert client.get("/runs").json() == []


def test_runs_of_the_investigator_are_listed_with_something_readable() -> None:
    client = client_for()
    run_investigation(client, site="demo-clinic", anomaly_id="A1")
    assert client.get("/runs").json()[0]["question"] == "Investigate A1 at demo-clinic"
