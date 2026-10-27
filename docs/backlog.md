# Backlog

The next small pieces of work, in order. Each item should fit in one pull request, and each is done only when its acceptance criteria are met by tests or a check we actually ran. Items marked done name the pull request that delivered them.

## Phase 1: model connection and the first agent

1. Project skeleton. A uv-managed Python 3.13 project with ruff, mypy and pytest configured. Done when `uv run pytest` passes with one smoke test and ruff and mypy report nothing. Done in #7.

2. Settings. Configuration loaded from environment variables, with clear errors for missing or unsafe values. Done when tests cover a valid load, a missing value and a rejected unsafe combination. Done in #11.

3. Synthetic meter data. A generator that produces the same readings for the same seed. Done when a test shows two runs with the same seed are identical and a different seed differs. Done in #13. The data is invented and not calibrated to any real site.

4. Energy calculations. Plain Python functions that convert kW to kWh and total energy per day, keeping power and energy apart. Done when tests cover the units and an interval edge case. Done in #15. Days are UTC days only.

5. Model interface and test adapter. One common interface for models, with a deterministic adapter that is clearly labelled as a test adapter. Done when tests run without network access and check streaming, usage reporting and error mapping. Done in #17.

6. Real providers. Adapters for the providers we have API access to. Done when one real call succeeds, provider errors map to our own errors, and no key appears in logs or output. Done for Google Gemini in #19 and for Nebius in #21, each with one real call run by hand. Streaming has only been tested against a fake transport, not live.

7. Energy analyst agent. One agent with typed input and output, a versioned prompt, a list of allowed tools, a timeout and a few evaluation cases. Done when the evaluation cases run and the numbers in answers come from the Python functions, not the model. Delivered as three pull requests: the fact sheet and its calculations (#23), the agent with its checks and scripted evaluation cases (#25), and a command that asks a real model the same kind of questions (issue #26). Status: first real run done by hand on 27 October 2026 against Gemini (gemini-flash-lite-latest) and Nebius (Qwen3-30B-A3B-Instruct-2507). Four of five questions passed the figure check on both. The summary question failed on both because of a bug in our own reply check (valid JSON with nine findings was reported as not JSON), fixed in issue #28 together with prompt v2. Reading the answers also showed weaker wording: Gemini once claimed the fact sheet had no weekend comparison when it did, and Nebius added findings that did not help answer the question. (Earlier notes blamed both models for missing spaces between words. That was wrong: the spaces were lost when copying wrapped lines from a terminal, so the command now has --save.) Second run with prompt v2: five of five passed on both providers, but reading the answers showed Nebius writing "the 13-day period" for 14 days of data, which the figure check could not see because it skipped numbers without an energy unit. Issue #30 adds a days-covered fact, checks day counts and adds prompt v3. Third run (Nebius only, prompt v3): the day count was right and all five passed, but the summary question needed a retry whose reason was not shown, which this change fixes, and the weekend answer still cited facts that did not help. Gemini has not been run with v3. The agent stays in draft until runs are saved with --save and their answers read. A local-only API that lists agents and runs them (issue #34, described in docs/api.md) lets a screen try the agent, and a stopgap playground page at /playground (issue #36) does so in a browser.

## Phase 2: MCP server and client

8. Read-only energy tool as an MCP server over a local transport. Done when the server and a client are each tested alone, with input and output schemas and validation. Done in issue #40 (docs/mcp.md): two read-only tools over stdio using the official SDK, tested in process and as a real subprocess, with schemas, argument validation and a check command. Only the demo data is reachable.

9. Agent uses the tool through MCP. Done when the agent answers from tool results and a test shows it cannot read a site outside the caller's access. Partly done in issue #42: the analyst fetches its fact sheet through the server, the spec's server and tool lists and the read-only requirement are enforced and tested, tool calls appear in the trace, and a direct path is kept for comparison. Not done: the test that it cannot read a site outside the caller's access, because there is no caller or sign-in yet, so that waits for the authentication phase. Today the only limit is the server's fixed list of two demo sites. It has also not yet been run against a real model through MCP.

## Phase 1 follow-ups

10. Report writer agent and agent registry. A second agent on a shared pipeline, an `AGENTS` registry capped at five, an API and playground that run either agent, and `live_eval --agent report-writer`. Done when scripted evaluation cases pass, the API refuses input that does not fit the agent, and the playground works in a real browser. Built on the branch `overnight/2026-10-27` and not yet merged. Not done: no real model has run the report writer, so its prompt is untested against Gemini or Nebius, and the first live run will probably find wording problems the way the analyst's did. The live cases cover both audiences and both demo sites but not a site with missing readings, because that needs custom data and only the scripted evaluation covers it.

11. Anomaly detector. A deterministic detector that compares each reading with the same time of day on other days of the same kind and merges unusual readings into events, with no model involved. Done when tests show it stays quiet on clean synthetic data across many seeds and finds injected spikes, drops and sustained changes. Built on the branch `overnight/2026-10-27`, described in [anomalies.md](anomalies.md). Not done: it has only seen synthetic data, its thresholds are uncalibrated, it is not yet an MCP tool, and the anomaly investigator that would use it is not built.

## Later phases

The remaining agents, the order they are built in and the rules they follow are described in [agents.md](agents.md).
