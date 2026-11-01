#!/usr/bin/env bash
set -euo pipefail
# Deploy a released image version: snapshot the DB, pull, start, smoke test, roll back on failure.
# Run on the homelab only.   Usage: ops/deploy.sh <version>   (e.g. 0.1.0, sha-1a2b3c4)
#   RC_IMAGE_SOURCE=pull   (default) pull the version's images from GHCR
#   RC_IMAGE_SOURCE=build  build them from this checkout, which must be at <version>

here=$(cd "$(dirname "$0")" && pwd)

# Host settings: RC_* keys from ops/.env, unless already set in the environment.
env_file=${RC_ENV_FILE:-$here/.env}
if [[ -f $env_file ]]; then
  while IFS='=' read -r key value; do
    [[ $key =~ ^RC_[A-Z_]+$ && -z ${!key:-} ]] || continue
    value=${value%%#*}
    value=${value%"${value##*[![:space:]]}"}
    export "$key=$value"
  done <"$env_file"
fi
state=${RC_STATE_DIR:-$here/../.deploy}
data=${RC_DATA_DIR:-$here/data}
export RC_DATA_DIR=$data

version=${1:-}
if [[ ! $version =~ ^([0-9]+\.[0-9]+\.[0-9]+|sha-[0-9a-f]{7,40})$ ]]; then
  echo "usage: deploy.sh <version>  (x.y.z or sha-<hex>)" >&2
  exit 2
fi

source=${RC_IMAGE_SOURCE:-pull}
case $source in
  pull) ;;
  build)
    head=$(git -C "$here" rev-parse HEAD)
    matches=no
    if [[ $version == sha-* ]]; then
      if [[ $head == "${version#sha-}"* ]]; then matches=yes; fi
    elif git -C "$here" tag --points-at HEAD | grep -qx "v$version"; then
      matches=yes
    fi
    if [[ $matches == no ]]; then
      echo "deploy: checkout $head does not match $version" >&2
      exit 2
    fi
    export RC_GIT_SHA=${head:0:7}
    ;;
  *)
    echo "deploy: RC_IMAGE_SOURCE must be pull or build" >&2
    exit 2
    ;;
esac

mkdir -p "$state"
current=$(cat "$state/current" 2>/dev/null || true)

if [[ -n $current ]]; then
  echo "$current" >"$state/previous"
  if [[ -f $data/retro-cable.db ]]; then
    cp "$data/retro-cable.db" "$state/db-$current.sqlite"
  fi
fi

export RC_VERSION=$version
if [[ $source == build ]]; then
  docker compose -f "$here/compose.yml" -f "$here/compose.build.yml" build
else
  docker compose -f "$here/compose.yml" pull
fi
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
