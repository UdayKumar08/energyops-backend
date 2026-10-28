#!/bin/sh
set -eu
case "${1:-serve}" in
  serve) exec python -m energyops.service --port "${PORT:-8100}" ;;
  bootstrap) exec python -m energyops.db.bootstrap ;;
  migrate) exec python -m energyops.db.setup ;;
  *) echo "Unknown command '$1'. Use serve, bootstrap or migrate." >&2; exit 64 ;;
esac
