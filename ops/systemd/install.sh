#!/usr/bin/env bash
set -euo pipefail
# Install Retro Cable's systemd timers on the mini: the weekly station manager (M5), the
# nightly backup and the Sunday report (Phase 8). One time, as root:
#   sudo ops/systemd/install.sh
# Idempotent: units are rewritten only when they differ from this checkout's, then systemd
# reloads and the timers are enabled. The service runs as the checkout's owner (the user who
# runs deploy.sh and docker), from this checkout.
#   ops/systemd/install.sh --check   no root; exit 1 (with a note) when the installed units
#                                    are missing or differ. deploy.sh runs this after a deploy.

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
dest=${RC_SYSTEMD_DIR:-/etc/systemd/system}
units=(retro-cable-station-manager.service retro-cable-station-manager.timer
  retro-cable-backup.service retro-cable-backup.timer
  retro-cable-weekly-report.service retro-cable-weekly-report.timer)
timers=(retro-cable-station-manager.timer retro-cable-backup.timer retro-cable-weekly-report.timer)
owner=${RC_SERVICE_USER:-$(stat -c %U "$repo" 2>/dev/null || stat -f %Su "$repo")}

render() {
  sed -e "s|/opt/retro-cable|$repo|g" -e "s|^User=CHECKOUT_OWNER\$|User=$owner|" "$here/$1"
}

if [[ ${1:-} == --check ]]; then
  command -v systemctl >/dev/null || exit 0 # not a systemd host (CI, a Mac)
  for unit in "${units[@]}"; do
    if ! render "$unit" | cmp -s - "$dest/$unit"; then
      echo "note: a Retro Cable timer ($unit) is missing or out of date; run once: sudo $here/install.sh" >&2
      exit 1
    fi
  done
  exit 0
fi

if [[ ! -w $dest ]]; then
  echo "install.sh: can't write $dest; run it with sudo" >&2
  exit 1
fi
changed=no
for unit in "${units[@]}"; do
  if ! render "$unit" | cmp -s - "$dest/$unit"; then
    render "$unit" >"$dest/$unit"
    changed=yes
  fi
done
if [[ $changed == yes ]]; then
  systemctl daemon-reload
fi
systemctl enable --now "${timers[@]}"
echo "timers installed (run as $owner from $repo); next runs:"
systemctl list-timers "${timers[@]}" --no-pager || true
