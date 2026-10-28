# EnergyOps AI (backend)

A multi-tenant energy operations platform for hotels, commercial buildings and small
industrial facilities. It will help customers monitor consumption and estimated cost,
detect unusual usage, investigate anomalies with AI agents and approved tools, and
prepare maintenance tickets that a person approves.

It is a decision-support platform. It does not control equipment or trade energy.

## Status

This repository holds a working draft of the whole platform on the branch `build/full-platform`, which has not been merged or deployed. It contains the Python backend (API, database and migrations, row level security, sign-in checks, five agents, MCP server, LangGraph approval flow, uploads, forecasting and knowledge search), a Next.js web app in `web/` with customer and platform admin screens, Dockerfiles, and Azure Terraform that has never been applied. [docs/status.md](docs/status.md) says exactly what has been verified, what was only tested with fakes and what has never been run. Read it before relying on any claim in the other documents.

## Requirements

Git, GitHub CLI, Python 3.13 (managed by uv), uv, Docker Desktop and VS Code.

## Working method

Small changes on a feature branch, one focused pull request each, checked before merging.
Secrets never go in this repository. Real values live in an ignored `.env` file locally
and in Azure Key Vault in the cloud.

## Documentation

See [docs/status.md](docs/status.md) for what works and what has not been run.

See [docs/architecture.md](docs/architecture.md) for the planned structure and the reasons behind it.

See [docs/backlog.md](docs/backlog.md) for what is planned next and how each item is judged done.

See [docs/agents.md](docs/agents.md) for the planned agents (at most five) and the rules they follow.
