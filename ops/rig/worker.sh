#!/usr/bin/env bash
set -euo pipefail
# The rig's conversion loop, run inside the worker container (ops/rig/compose.yml).
#   1. scan the NAS TV and movie trees (read-only) into the rig's own database (/data);
#   2. normalize into the media root (the NAS .retro-cable folder), carried shows first,
#      NVENC while the GPU is idle and x264 otherwise, writing a JSON sidecar per encode
#      and manifest.json for the mini (ingest/manifest.py);
#   3. sleep, then look again for new files.
# Everything runs at nice 19 and in the idle I/O class. Resumable: a stopped run picks up
# where it left off (finished items are recorded one by one).

tv=${RC_TV_ROOT:-/sources/tv}
movies=${RC_MOVIES_ROOT:-/sources/movies}
media=${RC_MEDIA_DIR:-/media}
data=${RC_DATA_DIR:-/data}
interval=${RC_WORKER_INTERVAL:-21600}
encoder=${RC_ENCODER:-auto}

log() { printf 'worker: %s\n' "$*" >&2; }
ingest() { nice -n 19 ionice -c 3 python -m retro_cable.ingest "$@"; }
has_files() { [[ -d $1 ]] && [[ -n $(find "$1" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null) ]]; }

cycle() {
  # The media root must be the NAS folder: it carries a .plexignore of '*'. Without it the
  # share isn't mounted, and nothing is written anywhere.
  if [[ ! -f $media/.plexignore ]]; then
    log "$media has no .plexignore: the .retro-cable share is not mounted; not writing"
    return 1
  fi
  # An empty source tree is an unmounted share; scanning it would detach every file.
  if ! has_files "$tv"; then
    log "$tv is empty: the TV share is not mounted; skipping this cycle"
    return 1
  fi
  local scan=(scan --tv "$tv" --no-probe)
  if has_files "$movies"; then
    scan+=(--movies "$movies")
  fi
  log "scan started"
  if ingest "${scan[@]}" >"$data/last-scan.json"; then
    log "scan done ($data/last-scan.json)"
  else
    log "scan failed; normalizing what is already known"
  fi
  log "normalize started (encoder $encoder)"
  if ingest normalize --all --order priority --encoder "$encoder" --manifest \
    >"$data/last-normalize.json"; then
    log "normalize done ($data/last-normalize.json)"
  else
    log "normalize failed"
    return 1
  fi
}

while true; do
  status=0
  cycle || status=$?
  if [[ ${RC_WORKER_ONCE:-} == 1 ]]; then
    exit "$status"
  fi
  if [[ $status -ne 0 ]]; then
    sleep 300
  else
    log "next look in ${interval}s"
    sleep "$interval"
  fi
done
