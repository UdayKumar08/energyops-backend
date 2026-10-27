# EnergyOps AI (backend)

A multi-tenant energy operations platform for hotels, commercial buildings and small
industrial facilities. It will help customers monitor consumption and estimated cost,
detect unusual usage, investigate anomalies with AI agents and approved tools, and
prepare maintenance tickets that a person approves.

It is a decision-support platform. It does not control equipment or trade energy.

## Status

Phase 0, foundation. Nothing is implemented yet. This repository will hold the Python
backend: API, database migrations, model adapters, agents, MCP servers and tests. The
web application will live in a separate repository.

## Requirements

Git, GitHub CLI, Python 3.13 (managed by uv), uv, Docker Desktop and VS Code.

## Working method

Small changes on a feature branch, one focused pull request each, checked before merging.
Secrets never go in this repository. Real values live in an ignored `.env` file locally
and in Azure Key Vault in the cloud.

## Documentation

See [docs/architecture.md](docs/architecture.md) for the planned structure and the reasons behind it.

See [docs/backlog.md](docs/backlog.md) for what is planned next and how each item is judged done.
