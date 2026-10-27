# Backlog

The next small pieces of work, in order. Each item should fit in one pull request, and each is done only when its acceptance criteria are met by tests or a check we actually ran. Items marked done name the pull request that delivered them.

## Phase 1: model connection and the first agent

1. Project skeleton. A uv-managed Python 3.13 project with ruff, mypy and pytest configured. Done when `uv run pytest` passes with one smoke test and ruff and mypy report nothing. Done in #7.

2. Settings. Configuration loaded from environment variables, with clear errors for missing or unsafe values. Done when tests cover a valid load, a missing value and a rejected unsafe combination. Done in #11.

3. Synthetic meter data. A generator that produces the same readings for the same seed. Done when a test shows two runs with the same seed are identical and a different seed differs. Done in #13. The data is invented and not calibrated to any real site.

4. Energy calculations. Plain Python functions that convert kW to kWh and total energy per day, keeping power and energy apart. Done when tests cover the units and an interval edge case. Done in #15. Days are UTC days only.

5. Model interface and test adapter. One common interface for models, with a deterministic adapter that is clearly labelled as a test adapter. Done when tests run without network access and check streaming, usage reporting and error mapping. Done in #17.

6. Real providers. Adapters for the providers we have API access to. Done when one real call succeeds, provider errors map to our own errors, and no key appears in logs or output. Done for Google Gemini in #19 and for Nebius in #21, each with one real call run by hand. Streaming has only been tested against a fake transport, not live.

7. Energy analyst agent. One agent with typed input and output, a versioned prompt, a list of allowed tools, a timeout and a few evaluation cases. Done when the evaluation cases run and the numbers in answers come from the Python functions, not the model. Delivered as three pull requests: the fact sheet and its calculations (#23), the agent with its checks and scripted evaluation cases (#25), and a command that asks a real model the same kind of questions (issue #26). Status: the scripted cases and the command are tested against the labelled test adapter only. The agent has not yet been run against a real model, so it stays in draft until that run has been done by hand and its answers read.

## Phase 2: MCP server and client

8. Read-only energy tool as an MCP server over a local transport. Done when the server and a client are each tested alone, with input and output schemas and validation.

9. Agent uses the tool through MCP. Done when the agent answers from tool results and a test shows it cannot read a site outside the caller's access.

## Later phases

The remaining agents, the order they are built in and the rules they follow are described in [agents.md](agents.md).
