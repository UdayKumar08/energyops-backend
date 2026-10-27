"""Start the local API:  uv run python -m energyops.api [--port 8000]

It listens on 127.0.0.1 only, and there is deliberately no option to change that.
"""

from __future__ import annotations

import argparse

import uvicorn

from energyops.api.app import ApiStartupRefused, create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m energyops.api", description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    try:
        app = create_app()
    except ApiStartupRefused as exc:
        raise SystemExit(f"Refusing to start: {exc}") from None
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
