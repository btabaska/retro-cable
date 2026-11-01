#!/usr/bin/env bash
set -euo pipefail
# Restore the previously deployed image version and its DB snapshot, then smoke test.
# Run by Hermes on the homelab only, automatically after a failed deploy or after you approve.

here=$(cd "$(dirname "$0")" && pwd)
state=${RC_STATE_DIR:-$here/../.deploy}
data=${RC_DATA_DIR:-$here/data}
export RC_DATA_DIR=$data

previous=$(cat "$state/previous" 2>/dev/null || true)
if [[ -z $previous ]]; then
  echo "rollback: no previous version recorded in $state" >&2
  exit 1
fi

export RC_VERSION=$previous
docker compose -f "$here/compose.yml" down
if [[ -f $state/db-$previous.sqlite ]]; then
  mkdir -p "$data"
  cp "$state/db-$previous.sqlite" "$data/retro-cable.db"
fi
docker compose -f "$here/compose.yml" up -d --wait
echo "$previous" >"$state/current"

RC_EXPECT_VERSION=$previous "$here/smoke.sh"
echo "rollback: $previous is live"
