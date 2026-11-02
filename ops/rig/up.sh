#!/usr/bin/env bash
set -euo pipefail
# Start or update the media worker on the rig, from a checkout (e.g. ~/retro-cable-worker):
#   git pull && ops/rig/up.sh
# Checks the three NAS mounts first: the sources must be read-only and the media root must
# be the NAS LiveTV share (RC_RIG_MEDIA_SHARE), so the worker can't write anywhere else. The container keeps
# running across logouts and reboots (restart: unless-stopped). Stop it with
#   docker compose -f ops/rig/compose.yml down

here=$(cd "$(dirname "$0")" && pwd)
if [[ -f $here/.env ]]; then
  set -a
  # shellcheck source=/dev/null
  . "$here/.env"
  set +a
fi
tv=${RC_RIG_TV:-/mnt/nas-tv-ro}
movies=${RC_RIG_MOVIES:-/mnt/nas-movies-ro}
media=${RC_RIG_MEDIA:-/mnt/nas-livetv}
share=${RC_RIG_MEDIA_SHARE:-LiveTV}
findmnt=${FINDMNT:-findmnt}

fail() { echo "up: $*" >&2; exit 1; }

options() { "$findmnt" -n -o OPTIONS --target "$1" -t cifs 2>/dev/null | head -n 1; }
source_of() { "$findmnt" -n -o SOURCE --target "$1" -t cifs 2>/dev/null | head -n 1; }

for dir in "$tv" "$movies" "$media"; do
  ls "$dir" >/dev/null 2>&1 || fail "$dir is not reachable (automount failed?)"
done
for dir in "$tv" "$movies"; do
  [[ ,$(options "$dir"), == *,ro,* ]] || fail "$dir must be a read-only CIFS mount"
done
[[ $(source_of "$media") == */"$share" ]] || fail "$media must be the NAS $share share"
[[ ,$(options "$media"), == *,rw,* ]] || fail "$media must be mounted read-write"
[[ -f $media/.plexignore ]] || fail "$media has no .plexignore; refusing to write there"

mkdir -p "${RC_RIG_DATA:-$here/data}"
docker compose -f "$here/compose.yml" up -d --build "$@"
docker compose -f "$here/compose.yml" ps
