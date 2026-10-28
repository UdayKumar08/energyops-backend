"""Start the platform API:  uv run python -m energyops.service [--port 8100]

Locally it listens on 127.0.0.1 only. In any other environment it listens on all interfaces,
because it runs in a container behind the platform's own ingress.
"""

from __future__ import annotations

import argparse

import uvicorn

from energyops.config import ConfigurationError, Environment, get_settings
from energyops.service.app import create_service_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m energyops.service", description=__doc__)
    parser.add_argument("--port", type=int, default=8100)
    args = parser.parse_args()
    try:
        settings = get_settings()
        app = create_service_app(settings)
    except ConfigurationError as exc:
        raise SystemExit(f"Refusing to start: {exc}") from None
    host = "127.0.0.1" if settings.environment is Environment.LOCAL else "0.0.0.0"  # noqa: S104
    uvicorn.run(app, host=host, port=args.port)


if __name__ == "__main__":
    main()
