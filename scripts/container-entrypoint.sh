#!/bin/sh
set -eu

case "${1:-api}" in
  api)
    heatshield migrate
    demo_value=$(printf '%s' "${HEATSHIELD_DEMO:-true}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')
    case "$demo_value" in
      1|true|yes) heatshield seed-demo ;;
    esac
    exec heatshield serve --host 0.0.0.0 --port 8000
    ;;
  worker)
    python /app/scripts/wait-for-database.py
    shift
    exec heatshield worker "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
