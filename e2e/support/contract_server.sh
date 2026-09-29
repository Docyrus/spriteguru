#!/usr/bin/env bash
# An isolated spriteguru-web dev server for the cloud contract run (cloud plan 12): its own local
# database in a temp folder, on its own port, so the developer's own dev server and data are never
# touched. Prints the URL and the log path to pass to pytest, then keeps running until stopped.
#   e2e/support/contract_server.sh [port] [path to spriteguru-web]
set -euo pipefail
port="${1:-5189}"
web="${2:-$(cd "$(dirname "$0")/../../../spriteguru-web" && pwd)}"
state="$(mktemp -d)/contract-state"
log="$(dirname "$state")/server.log"
mkdir -p "$state"
cd "$web"
npx wrangler d1 migrations apply DB --local --persist-to "$state" > /dev/null
echo "cloud url: http://localhost:$port"
echo "log:       $log"
echo "run:       uv run pytest e2e -k contract --cloud-url http://localhost:$port --cloud-log $log"
# --local-upstream: without it wrangler rewrites requests (and their Origin) to the production route
exec npx wrangler dev --port "$port" --ip 127.0.0.1 --local-upstream "localhost:$port" --persist-to "$state" \
  --var "APP_URL:http://localhost:$port" --local > "$log" 2>&1
