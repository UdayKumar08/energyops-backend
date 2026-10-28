"""Write the platform API's OpenAPI description to a file, so the web application's types are
generated from the API itself and cannot drift from it.

    uv run python -m energyops.service.openapi web/openapi.json
    uv run python -m energyops.service.openapi --check web/openapi.json   # fails if out of date
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import SecretStr

from energyops.config import Settings
from energyops.service.app import create_service_app


def render() -> str:
    # Nothing connects to a database when the app is only described, so a placeholder URL is fine.
    settings = Settings(
        database_url=SecretStr("postgresql+psycopg://x:x@localhost:5432/x"),
        local_jwt_secret=SecretStr("o" * 48),
    )
    schema = create_service_app(settings).openapi()
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if len(paths) != 1:
        print("Usage: python -m energyops.service.openapi [--check] PATH", file=sys.stderr)
        return 2
    target = Path(paths[0])
    text = render()
    if check:
        if not target.exists() or target.read_text() != text:
            print(
                f"{target} is out of date. Regenerate it with: "
                f"python -m energyops.service.openapi {target}"
            )
            return 1
        return 0
    target.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
