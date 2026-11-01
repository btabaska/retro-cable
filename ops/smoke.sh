#!/usr/bin/env bash
# Post-deploy smoke test: web app, /api/health and /api/version.
#   RC_SMOKE_URL       base URL (default http://127.0.0.1:8080)
#   RC_EXPECT_VERSION  if set, /api/version must report it
set -euo pipefail

base=${RC_SMOKE_URL:-http://127.0.0.1:8080}
retries=${RC_SMOKE_RETRIES:-10}
delay=${RC_SMOKE_DELAY:-3}

fetch() {
  local path=$1 attempt
  for ((attempt = 1; attempt <= retries; attempt++)); do
    if curl -fsS --max-time 5 "$base$path"; then
      return 0
    fi
    sleep "$delay"
  done
  echo "smoke: GET $path failed after $retries attempts" >&2
  return 1
}

fetch / | grep -q 'id="root"' || { echo "smoke: web app missing" >&2; exit 1; }
fetch /api/health | grep -q '"status":"ok"' || { echo "smoke: unhealthy" >&2; exit 1; }
version_json=$(fetch /api/version)
if [[ -n ${RC_EXPECT_VERSION:-} ]] && ! grep -q "\"version\":\"$RC_EXPECT_VERSION\"" <<<"$version_json"; then
  echo "smoke: expected version $RC_EXPECT_VERSION, got $version_json" >&2
  exit 1
fi
echo "smoke: ok $version_json"
