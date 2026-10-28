# The platform API. This file has been written but not built: no container engine was available
# where it was written. The CI image-build step (see .github/workflows/deploy.yml) is the first
# real build.
#
# One image does two jobs, chosen by its command:
#   serve    start the API (the default)
#   bootstrap  once per new database: create the owner and application roles and the extension.
#              Needs the server administrator's URL.
#   migrate    bring the database up to date, then stop. Needs the owner role's URL.
# The API itself is given only the application role's URL.
FROM python:3.13-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev

FROM python:3.13-slim-bookworm
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin energyops
WORKDIR /app
COPY --from=build --chown=energyops:energyops /app /app
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 ENERGYOPS_ENVIRONMENT=nonprod
USER energyops
EXPOSE 8100
ENTRYPOINT ["entrypoint.sh"]
CMD ["serve"]
