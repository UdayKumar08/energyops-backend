# Architecture notes

This document describes how EnergyOps AI is planned to be structured and why. Nothing described here is implemented yet. As each part is built and tested, this page will say so.

## What we are building

EnergyOps AI helps the people who run hotels, commercial buildings and small industrial sites understand their energy use. A facilities manager should be able to see consumption and estimated cost, be alerted to unusual usage, ask an assistant why it happened, and approve a maintenance ticket. It supports decisions. It does not control equipment or trade energy.

## One backend, clear boundaries

The backend is a modular monolith: one Python application, one codebase and one repository, divided into folders with firm boundaries. We chose this over several services because a single developer gains little from the extra coordination, and because the permission and tenant-isolation code is shared by almost everything. A web application will live in a separate repository, since it has a different language, toolchain and deployment target. If a piece later needs to stand alone, because another team owns it or it must be locked down separately, the folder boundaries make that a mechanical move.

The planned layout inside the backend package is the following.

    api/        HTTP endpoints, request validation and error format
    db/         tables, migrations and database sessions
    security/   authentication, roles, permissions and site-level access
    energy/     ingestion, units, tariffs and anomaly detection in plain Python
    models/     model providers, model registry, usage and budgets
    agents/     one folder per agent: prompt, typed input and output, tools
    workflows/  LangGraph workflows, state, checkpoints and approvals
    mcp/        MCP servers for energy data, knowledge and incidents
    jobs/       durable background work

Two rules keep these folders from tangling. Calculations, permissions and database writes are ordinary Python and never left to a model, which is only used to interpret and explain. And nothing that arrives from a model or a tool can decide who the user is or which organisation they belong to; that always comes from the authenticated session.

## Environments

LOCAL is a reproducible developer setup with synthetic data and optional real provider connections. DEV is a shared integration environment. NONPROD is a stable environment for release validation, user acceptance testing and demonstrations. PROD is a future customer environment that needs its own approval and operational readiness. Development code never connects to production by default, and real customer data is never copied into development without an explicitly approved process.

## Status

Implemented: the project skeleton and CI, settings, a synthetic meter data generator, energy calculations, a common model interface with a labelled test adapter, and adapters for Google Gemini and Nebius. Nothing else exists yet. Planned next: the energy analyst agent, then an MCP server, multi-agent workflows, the database and API, authentication, the customer interface, administration, data and forecasting, voice, and Azure deployment, in that order. The planned agents are described in agents.md.
