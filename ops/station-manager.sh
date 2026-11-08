#!/usr/bin/env bash
set -euo pipefail
# The weekly station manager (M5): `python -m retro_cable.station_manager run` inside the
# running api container, against the live database. Run by the systemd timer in ops/systemd/
# (Sundays 2 AM New York time); by hand: ops/station-manager.sh [--dry-run] [--weeks N].
# Never during a deploy: deploy.sh holds the same lock while it restarts the stack (and
# may roll the database back), so this waits for it.

here=$(cd "$(dirname "$0")" && pwd)
state=${RC_STATE_DIR:-$here/../.deploy}
mkdir -p "$state"
compose=(docker compose -f "$here/compose.yml")

if command -v flock >/dev/null; then
  exec 9>"$state/deploy.lock"
  if ! flock -w "${RC_LOCK_WAIT:-1800}" 9; then
    echo "station-manager: a deploy still holds $state/deploy.lock; not run" >&2
    exit 75
  fi
else
  echo "station-manager: flock not found; not guarding against a running deploy" >&2
fi

# After a reboot (the timer catches up on a missed Sunday) the stack may still be starting.
tries=${RC_API_WAIT:-20}
until "${compose[@]}" exec -T api true >/dev/null 2>&1; do
  tries=$((tries - 1))
  if ((tries <= 0)); then
    echo "station-manager: the api container isn't running; not run" >&2
    exit 1
  fi
  sleep "${RC_API_WAIT_DELAY:-30}"
done

exec "${compose[@]}" exec -T api python -m retro_cable.station_manager run "$@"
