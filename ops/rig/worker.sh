#!/usr/bin/env bash
set -euo pipefail
# The rig's conversion loop, run inside the worker container (ops/rig/compose.yml).
#   1. scan the NAS TV and movie trees (Plex's, read-only) and the Live TV downloads in the
#      LiveTV share (sources/tv, sources/movies: sonarr-livetv and radarr-livetv) into the
#      rig's own database (/data);
#   2. only with RC_ACQUIRE=1: ask sonarr-livetv and radarr-livetv for carried titles that
#      have no files anywhere (ingest/acquire.py);
#   3. normalize into the media root (the NAS LiveTV share), in HEVC: only what a channel
#      carries (YAML, /admin here, and the mini's /admin with RC_PRIORITY_URL), earliest first,
#      NVENC while the GPU is idle and x264 otherwise, writing a JSON sidecar per encode
#      and manifest.json for the mini (ingest/manifest.py). Originals under
#      RC_DELETE_SOURCES_UNDER (the downloads) are deleted once their encode is whole;
#   4. join TV movies TheTVDB files as episodes of a series from those episodes' encodes
#      (ingest/assemble.py), once all of them are converted;
#   5. sleep, then look again for new files. Each normalize run gets RC_NORMALIZE_MINUTES
#      (default 6 h); one that stopped with work left is followed by a new cycle a minute
#      later, so downloads that arrive during a long backlog are scanned within hours.
# Everything runs at nice 19 and in the idle I/O class. Resumable: a stopped run picks up
# where it left off (finished items are recorded one by one).

tv=${RC_TV_ROOT:-/sources/tv}
movies=${RC_MOVIES_ROOT:-/sources/movies}
media=${RC_MEDIA_DIR:-/media}
livetv_tv=${RC_LIVETV_TV_ROOT:-$media/sources/tv}
livetv_movies=${RC_LIVETV_MOVIES_ROOT:-$media/sources/movies}
data=${RC_DATA_DIR:-/data}
interval=${RC_WORKER_INTERVAL:-21600}
encoder=${RC_ENCODER:-auto}
minutes=${RC_NORMALIZE_MINUTES:-360}
unfinished=0

log() { printf 'worker: %s\n' "$*" >&2; }
ingest() { nice -n 19 ionice -c 3 python -m retro_cable.ingest "$@"; }
has_files() { [[ -d $1 ]] && [[ -n $(find "$1" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null) ]]; }

cycle() {
  # The media root must be the NAS folder: it carries a .plexignore of '*'. Without it the
  # share isn't mounted, and nothing is written anywhere.
  if [[ ! -f $media/.plexignore ]]; then
    log "$media has no .plexignore: the LiveTV share is not mounted; not writing"
    return 1
  fi
  # An empty Plex TV tree is an unmounted share; scanning it would detach every file.
  if ! has_files "$tv"; then
    log "$tv is empty: the TV share is not mounted; skipping this cycle"
    return 1
  fi
  local scan=(scan --tv "$tv" --no-probe)
  # The Live TV downloads live in the media share, checked above, so an empty folder there
  # really is empty (everything converted and its originals deleted): scan it anyway.
  if [[ -d $livetv_tv ]]; then
    scan+=(--tv "$livetv_tv")
  fi
  if has_files "$movies"; then
    scan+=(--movies "$movies")
  fi
  if [[ -d $livetv_movies ]]; then
    scan+=(--movies "$livetv_movies")
  fi
  log "scan started"
  if ingest "${scan[@]}" >"$data/last-scan.json"; then
    log "scan done ($data/last-scan.json)"
  else
    log "scan failed; normalizing what is already known"
  fi
  # Off unless RC_ACQUIRE=1: nothing is requested until the show lineup is final.
  if [[ ${RC_ACQUIRE:-0} == 1 ]]; then
    log "acquire started"
    if ingest acquire --livetv-tv "$livetv_tv" >"$data/last-acquire.json"; then
      log "acquire done ($data/last-acquire.json)"
    else
      log "acquire failed or refused ($data/last-acquire.json); normalizing anyway"
    fi
  fi
  log "normalize started (encoder $encoder, up to $minutes min)"
  local status=0
  unfinished=0
  # The report goes in place only once the run is over: the previous one stays readable for
  # the hours this one takes.
  if ingest normalize --order priority --encoder "$encoder" --manifest --for-minutes "$minutes" \
    >"$data/last-normalize.json.part"; then
    mv "$data/last-normalize.json.part" "$data/last-normalize.json"
    log "normalize done ($data/last-normalize.json)"
    if grep -q '"unfinished": true' "$data/last-normalize.json"; then
      unfinished=1
    fi
  else
    mv "$data/last-normalize.json.part" "$data/last-normalize.json"
    log "normalize failed"
    status=1
  fi
  # TV movies TheTVDB files as episodes (overrides.yaml episodes_of): joined from their
  # episodes' encodes once all are converted (ingest/assemble.py).
  log "assemble started"
  if ingest assemble --manifest >"$data/last-assemble.json"; then
    log "assemble done ($data/last-assemble.json)"
  else
    log "assemble failed ($data/last-assemble.json)"
  fi
  return "$status"
}

while true; do
  status=0
  cycle || status=$?
  if [[ ${RC_WORKER_ONCE:-} == 1 ]]; then
    exit "$status"
  fi
  if [[ $status -ne 0 ]]; then
    sleep 300
  elif [[ $unfinished == 1 ]]; then
    log "normalize stopped with work left; next look in 60s"
    sleep 60
  else
    log "next look in ${interval}s"
    sleep "$interval"
  fi
done
