#!/usr/bin/env bash
set -euo pipefail
# Post-deploy smoke test: web app, /api/health, /api/version and the info banner's
# lineup and tune requests.
#   RC_SMOKE_YEAR      simulated year for the banner check (default 1998)
#   RC_SMOKE_URL       base URL (default http://127.0.0.1:$RC_WEB_PORT, port 8080 if unset)
#   RC_EXPECT_VERSION  if set, /api/version must report it: x.y.z checks the version,
#                      sha-<hex> checks the commit

base=${RC_SMOKE_URL:-http://127.0.0.1:${RC_WEB_PORT:-8080}}
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
expected=${RC_EXPECT_VERSION:-}
if [[ $expected == sha-* ]]; then
  pattern="\"commit\":\"${expected:4:7}"
else
  pattern="\"version\":\"$expected\""
fi
if [[ -n $expected ]] && ! grep -q "$pattern" <<<"$version_json"; then
  echo "smoke: expected version $RC_EXPECT_VERSION, got $version_json" >&2
  exit 1
fi

# The info banner's requests on power-on: the lineup, then tune the first channel on the
# air. One try each: the api warms up before it serves, so once /api/health has answered
# a failure here is real, not warm-up (no retry to hide it).
year=${RC_SMOKE_YEAR:-1998}
once() {
  curl -fsS --max-time 10 "$base$1" || { echo "smoke: GET $1 failed" >&2; return 1; }
}
lineup=$(once "/api/channels?year=$year") || exit 1
# ChannelInfo has no nested objects, so each channel is one {...} run.
channel=$(grep -oE '"id":"[^"]+"[^}]*"on_air":true' <<<"$lineup" | head -n 1 | cut -d'"' -f4 || true)
if [[ -z $channel ]]; then
  echo "smoke: no channel on the air in $year" >&2
  exit 1
fi
once "/api/tune/$channel?year=$year" | grep -q '"current":{' \
  || { echo "smoke: nothing airing on $channel in $year" >&2; exit 1; }
echo "smoke: ok $version_json, $channel tunes in $year"
