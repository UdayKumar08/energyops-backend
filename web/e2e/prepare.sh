#!/usr/bin/env bash
# Prepares a throwaway local database for the browser tests: creates `energyops_e2e` if it is
# missing, empties it, migrates it and loads the invented demo data. Local only.
set -euo pipefail
cd "$(dirname "$0")/../.."

# A superuser creates the database and the extension. Locally that is the compose database's
# `energyops` user; CI sets E2E_SUPERUSER=postgres:postgres.
SUPERUSER="${E2E_SUPERUSER:-energyops:energyops}"
SUPER="postgresql://${SUPERUSER}@127.0.0.1:5433/postgres"
psql "$SUPER" -tc "select 1 from pg_database where datname='energyops_e2e'" | grep -q 1 \
  || psql "$SUPER" -c "create database energyops_e2e owner energyops_owner"
psql "postgresql://${SUPERUSER}@127.0.0.1:5433/energyops_e2e" -c "create extension if not exists vector"
psql "postgresql://energyops_owner:energyops_owner@127.0.0.1:5433/energyops_e2e" \
  -c "drop owned by current_user cascade"

export ENERGYOPS_ENVIRONMENT=local
export ENERGYOPS_ADMIN_DATABASE_URL="postgresql+psycopg://energyops_owner:energyops_owner@127.0.0.1:5433/energyops_e2e"
uv run python -m energyops.db.setup --seed
