# Database

The product keeps its data in PostgreSQL 16 with the pgvector extension. The code reaches it through SQLAlchemy 2 in async mode with the psycopg 3 driver, and the schema is changed only by Alembic migrations in `src/energyops/db/migrations`. Locally the database runs in Docker (`docker compose up -d db`, which listens on 127.0.0.1:5433), and the tests run against a real PostgreSQL, not a stand-in, because the part most worth testing is what the database itself refuses.

## Two roles

The owner role (`energyops_owner`) owns the tables and is used only to run migrations and the seed script, through `ENERGYOPS_ADMIN_DATABASE_URL`. The application role (`energyops_app`) is what the running code uses, through `ENERGYOPS_DATABASE_URL`. It owns nothing, is not a superuser, cannot create or alter tables and cannot switch row level security off, and a test checks each of those. The pgvector extension has to be created by a privileged role before migrations run, because PostgreSQL does not let an ordinary owner do it. The local setup does this in `docker/initdb/01_local_roles.sql`, whose passwords are throwaway values for a database on your own computer.

## Who can see what

Every table that holds a customer's data carries an `org_id`, and a row level security policy lets a session see and write only the rows whose `org_id` equals a setting on the session. `Database.session(scope)` opens a transaction and sets that value from the `Scope` it is given, and the value lasts only for that transaction, so a pooled connection never carries one person's organisation into the next request. The scope has to come from the signed-in person. It never comes from a model, a tool or the request body.

The repository functions in `energyops/db/repo.py` also put the organisation in every query, so a mistake in one layer is caught by the other. The tests prove the database layer on its own by running queries with no filter at all: a session for one organisation sees only that organisation's sites, readings, tariffs and runs, a session with no scope sees nothing, an insert that claims another organisation's id is refused, and updates and deletes aimed at another organisation touch no rows.

The product's own staff are a special case. A platform scope can read organisations, people and the audit log across organisations, and nothing else: a test confirms that a platform session sees no readings, sites, tariffs or runs. Signing in needs this scope for one step, finding the person by email or identity before their organisation is known, and then reads the rest inside the person's own organisation.

A person's access can be limited to some of an organisation's sites. A site they may not see looks exactly like a site that does not exist, so they cannot probe for names.

## The tables

Organisations, people (with a role and an optional list of sites), sites, meters (one primary meter per site, enforced by an index), readings (average power in kW per interval, with the database refusing impossible values), tariffs (one per site, with price bands stored as JSON), agent settings (administrator overrides of an agent's defaults, readable by everyone and writable only by the platform), runs (the whole run record with its trace, plus the main fields as columns) and the audit log. Later migrations add tickets, documents and ingestion records.

## Running it

Start the database with `docker compose up -d db`, then apply the migrations and load the demo data.

    export ENERGYOPS_ADMIN_DATABASE_URL=postgresql+psycopg://energyops_owner:energyops_owner@127.0.0.1:5433/energyops_dev
    uv run alembic upgrade head
    uv run python -m energyops.db.seed

The seed creates two invented organisations so that isolation can be seen: Harbour Hotels with two sites and four people, and Northside Health with one site and two people, each with 28 days of invented readings and an invented tariff, plus one platform account. It only runs in the local environment and can be run twice safely.

## What is not done

Row level security covers the tables above, but LangGraph's own checkpoint tables (added with the supervisor) are not protected by it, so access to a saved conversation is checked in code. All times are UTC. The migration tests run the migration up and down on an empty database but have not been run against Azure Database for PostgreSQL, where the extension is enabled through an allow list and roles are managed differently.

## Usage totals for the platform

Platform staff cannot read customer rows, and runs are customer rows. They still need to see how much the product is used, so migration 0002 adds one function, `platform_usage(since)`. It runs with its owner's rights, checks the platform setting itself, and returns only counts and token totals per organisation, agent and day: no inputs, outputs, site names or people. A session for an organisation, or one with no scope at all, gets an empty result, and a test checks both. Token counts are what the provider reported, or an estimate where it did not, and are not a bill.

## Tickets and the saved state of the approval graph

Migration 0003 adds the `tickets` table, which is a tenant table like the others: it carries an organisation id, row level security keeps each organisation to its own rows, and the repository filters by the sites a person may see. A ticket's id is also the id of its approval graph's saved state. LangGraph keeps that state in four tables of its own (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes` and `checkpoint_migrations`), which its saver creates and which the application role cannot create, so `setup_checkpointer` runs them as the owner role after the migrations. These four tables have no organisation column and so no row level security. Access is controlled in code: the service only resumes a graph for a ticket id that the caller has just read through the protected tickets table, with the row locked. A test lists the unprotected tables and fails if anything other than those four appears.
