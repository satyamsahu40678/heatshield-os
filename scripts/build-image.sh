#!/bin/sh
# Portable build; managed cloud CA mounts are optional and never become image layers.
set -eu
cd "$(dirname "$0")/.."
set -- build --tag "${HEATSHIELD_IMAGE:-heatshield-os:local}"
if [ -n "${CODEX_PROXY_CERT:-}" ]; then
  if [ ! -r "$CODEX_PROXY_CERT" ]; then
    echo 'CODEX_PROXY_CERT is set but unreadable.' >&2
    exit 1
  fi
  set -- "$@" --secret "id=proxy_ca,src=$CODEX_PROXY_CERT"
  if [ -r /etc/ssl/certs/ca-certificates.crt ]; then
    set -- "$@" --secret 'id=ca_bundle,src=/etc/ssl/certs/ca-certificates.crt'
  fi
fi
exec docker "$@" .
