#!/usr/bin/env bash
# Deploy a released image version: snapshot the DB, pull, start, smoke test, roll back on failure.
# Run by Hermes on the homelab only.   Usage: ops/deploy.sh <version>   (e.g. 0.1.0, sha-1a2b3c4)
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
state=${RC_STATE_DIR:-$here/../.deploy}
data=${RC_DATA_DIR:-$here/data}
export RC_DATA_DIR=$data

version=${1:-}
if [[ ! $version =~ ^([0-9]+\.[0-9]+\.[0-9]+|sha-[0-9a-f]{7,40})$ ]]; then
  echo "usage: deploy.sh <version>  (x.y.z or sha-<hex>)" >&2
  exit 2
fi

mkdir -p "$state"
current=$(cat "$state/current" 2>/dev/null || true)

if [[ -n $current ]]; then
  echo "$current" >"$state/previous"
  if [[ -f $data/retro-cable.db ]]; then
    cp "$data/retro-cable.db" "$state/db-$current.sqlite"
  fi
fi

export RC_VERSION=$version
docker compose -f "$here/compose.yml" pull
docker compose -f "$here/compose.yml" up -d --wait

if RC_EXPECT_VERSION=$version "$here/smoke.sh"; then
  echo "$version" >"$state/current"
  echo "deploy: $version is live"
  exit 0
fi

echo "deploy: smoke test failed for $version" >&2
if [[ -n $current ]]; then
  "$here/rollback.sh" || true
fi
exit 1
