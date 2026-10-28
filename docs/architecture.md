# Architecture notes

This document describes how EnergyOps AI is structured and why. What has actually been verified is in [status.md](status.md).

## What we are building

EnergyOps AI helps the people who run hotels, commercial buildings and small industrial sites understand their energy use. A facilities manager should be able to see consumption and estimated cost, be alerted to unusual usage, ask an assistant why it happened, and approve a maintenance ticket. It supports decisions. It does not control equipment or trade energy.

## One backend, clear boundaries

The backend is a modular monolith: one Python application, one codebase and one repository, divided into folders with firm boundaries. We chose this over several services because a single developer gains little from the extra coordination, and because the permission and tenant-isolation code is shared by almost everything. The web application lives in the `web` folder for now, with its own toolchain and no imports from the Python side, so it can move to its own repository with its history when that is worth doing. If a piece later needs to stand alone, because another team owns it or it must be locked down separately, the folder boundaries make that a mechanical move.

The layout inside the backend package is the following.

    api/        the local-only playground API (no sign-in, localhost only)
    service/    the platform API: routes, dependencies, application state
    security/   tokens (local and Microsoft Entra), roles, permissions, site access
    db/         tables, migrations, repository functions, setup and bootstrap commands
    agents/     the five agents: prompts, typed input and output, checks, registry
    graphs/     LangGraph graphs (the ticket approval flow)
    mcp_servers/ the read-only energy MCP server
    models/     model providers (test adapter, Gemini, Nebius) and credentials

Calculations (calculations, tariffs, savings, anomalies, forecast, ingest, knowledge) are plain modules at the top of the package.

Two rules keep these folders from tangling. Calculations, permissions and database writes are ordinary Python and never left to a model, which is only used to interpret and explain. And nothing that arrives from a model or a tool can decide who the user is or which organisation they belong to; that always comes from the authenticated session.

## Environments

LOCAL is a reproducible developer setup with synthetic data and optional real provider connections. DEV is a shared integration environment. NONPROD is a stable environment for release validation, user acceptance testing and demonstrations. PROD is a future customer environment that needs its own approval and operational readiness. Development code never connects to production by default, and real customer data is never copied into development without an explicitly approved process.

## The web app and the backend

The browser never talks to the backend. The Next.js server holds the backend's token in a cookie that page scripts cannot read, calls the backend on the person's behalf, and forwards the browser's own calls through one proxy route that allows only the product's API paths, requires a same-origin request for anything that changes data, and adds the token on the server. The backend decides what a person may see from its own database on every request. The web menu follows the role only to avoid showing links that would fail, and is not a control.

## Deployment

A single image runs the API, and the same image runs two manual jobs: one that creates the database roles once, and one that runs migrations. The API is only ever given the restricted application role's connection string. The Azure design is in [infra/README.md](../infra/README.md). It has never been applied.

## Status

See [status.md](status.md).
