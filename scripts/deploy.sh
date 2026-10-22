#!/bin/sh
set -eu

if ! command -v tailscale >/dev/null 2>&1; then
  echo "Tailscale CLI is required. Install and connect Tailscale before deploying." >&2
  exit 1
fi

PAPER_TRACKER_BIND_IP="$(tailscale ip -4 | sed -n '1p')"
if [ -z "$PAPER_TRACKER_BIND_IP" ]; then
  echo "Could not detect the server's Tailscale IPv4. Check that Tailscale is connected." >&2
  exit 1
fi

export PAPER_TRACKER_BIND_IP
export PAPER_TRACKER_PORT=8899

if [ "$#" -eq 0 ]; then
  set -- up -d
fi

exec docker compose "$@"
