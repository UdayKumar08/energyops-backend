# EnergyOps web app

The customer and platform-admin interface for EnergyOps AI. It is a Next.js app that talks to the
FastAPI backend only from the server. The browser never calls the backend and never holds a
backend token in a form that page scripts can read.

## How sign-in and API calls work

Signing in stores the backend's token in an httpOnly, SameSite=Lax cookie called `eo_session`.
Pages fetch data on the server with that token. Interactive parts of a page call `/api/...` on the
web app's own origin, and the route in `src/app/api/[...path]/route.ts` forwards the call to the
backend after three checks: the path must be on an allowlist (sign-in, health, docs and the OpenAPI
files are not reachable through it), a write must come from the same origin, and the token is added
on the server.

Locally, sign-in is the development one: you type a seeded email address and the backend, running
in the `local` environment, issues a local token. When `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`,
`ENTRA_CLIENT_SECRET` and `PUBLIC_URL` are all set, the app instead sends people to Microsoft and
swaps the returned code for an access token on the server (authorization code flow with PKCE),
and the development sign-in is switched off. The Entra flow is unit-tested only. It has not been
run in a browser or against Microsoft, so do not rely on it until it has.

## Running it

Start the backend first (see the root README), then:

    npm ci
    npm run dev

`BACKEND_URL` (server-only) defaults to `http://127.0.0.1:8100`. Seed demo data with
`uv run python -m energyops.db.setup --seed` from the repository root.

## Checks

    npm run lint
    npm run typecheck
    npm test            # unit tests
    npm run build
    npm run e2e         # real browser against the real backend and an empty database

`npm run e2e` expects the database prepared by `bash e2e/prepare.sh` (it needs a Postgres
superuser; set `E2E_SUPERUSER=user:password` if yours is not the local default). Set
`CHROMIUM_PATH` to use a Chromium that is already installed instead of Playwright's own.

## API types

`src/api/schema.d.ts` is generated from `openapi.json`, which the backend produces with
`uv run python -m energyops.service.openapi web/openapi.json`. After the API changes, regenerate
both (`npm run gen:api` does the second step). A backend test and a CI step fail when they have
drifted apart.
