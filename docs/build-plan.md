# Build plan for the full platform

This page records the decisions behind the long build on the branch `build/full-platform`, written before the work was done so that anyone can compare the plan with what was actually delivered. The honest account of what works, what was only tested with fakes and what was never run is in [status.md](status.md), which is written last.

## Ground rules that applied to every step

Nothing was merged, no pull request was opened, and nothing was deployed. No paid Azure resource was created. The Azure files in this branch are written and never applied. No provider key exists in the environment this work was done in, so no new agent has been run against a real model here, and every claim about model behaviour comes from scripted replies. Keys stay in a local `.env`, never reach the browser and never appear in the repository. The company names Uday works with are not used anywhere in the product.

## Decisions taken without asking

The web application lives in a `web` folder of this repository for now, because an unmerged branch is much easier to review as one piece. It has its own package file and toolchain and does not import anything from the Python side, so it can be moved into its own repository later with its history.

The database is PostgreSQL 16 with the pgvector extension, reached through SQLAlchemy 2 in async mode with the psycopg 3 driver, and migrated with Alembic. The application connects as a restricted role that owns nothing. A separate owner role runs migrations. Every table that holds customer data carries an organisation id and has a row level security policy that compares it with a setting the application sets for each request from the authenticated user. The repository code also filters by organisation, so a mistake in one layer is caught by the other. A test connects as the restricted role and proves that a query with no filter at all still sees only one organisation.

Sign-in uses signed tokens. In the local environment the backend issues short tokens for a seeded user, signed with the local secret that the settings already allow only in that environment. Everywhere else the backend accepts only Microsoft Entra ID access tokens, checking the signature against the tenant's published keys, the issuer, the tenant id, the audience and the expiry. The token proves who the person is. Which organisation they belong to, their role and the sites they may see come from our own database, never from the token body and never from a model or a tool. The Entra validator is tested with keys generated for the test. It has not been run against a real Entra tenant.

Roles are platform admin, organisation admin, manager, technician and viewer. A platform admin runs the product and can manage organisations, users and agent settings but cannot read any customer's readings, documents or tickets. The other four roles belong to one organisation and can be limited to some of its sites.

The five agents are the energy analyst, the report writer, the anomaly investigator, the savings advisor and the supervisor. Forecasting, knowledge search, voice and tickets are capabilities. The supervisor is a LangGraph graph that routes a question to the right agent or capability, or declines. A maintenance ticket is prepared by a graph that stops and waits for a person to approve it, with its state saved in Postgres so the wait survives a restart. Approving records the decision in our database. Nothing is sent to an outside ticketing system.

Confidence in the anomaly investigator now needs a stated reason that cites facts, and the label medium is accepted only with one. This is the first option offered in the last review, chosen because the live runs showed labels given with no reason.

Cost and savings figures come from a tariff stored for each site. If a site has no tariff the savings advisor says so and stops, and the product never invents a price. The demo sites get a clearly labelled invented tariff. Forecasting is a plain seasonal profile method that is scored against held back days, and the error is shown with every forecast. Knowledge search is extractive: it returns the matching passages with their source and never has a model write an answer from them. Embeddings come from a hashing embedder that is labelled as a test tool for local use, or from any OpenAI compatible embeddings endpoint, which is covered by tests with a fake transport only.

Voice uses the speech features built into the browser, so no audio reaches our servers. It only works in browsers that provide them. Server side speech needs a paid Azure resource and is left as a design note.

The browser never talks to the backend directly. The Next.js server keeps the session in a cookie the page script cannot read, and forwards calls to the backend. Provider keys exist only on the backend.

## Order of work

The savings advisor and the confidence change come first because they complete the agent set. The database and tenancy layer follows, then sign-in and the platform API with the isolation tests, then run storage and the admin settings. The supervisor and the approval graph come next, then data upload, forecasting and knowledge search. The web application is built after the API it needs exists, and the Azure files and the final documentation come last.
