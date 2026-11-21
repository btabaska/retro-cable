#!/usr/bin/env bash
set -euo pipefail
# Nightly backup (Phase 8): copy the live database inside the api container
# (`python -m retro_cable.backup take`, kept in the data dir's backups/), copy it to the NAS
# folder RC_BACKUP_DIR, prune the NAS copies by the same retention rule, then restore-test the
# NAS copy. Run by the systemd timer in ops/systemd/ (1 AM New York time, before the mini's restic run takes it offsite); by hand:
# ops/backup.sh. Never during a deploy: deploy.sh holds the same lock while it restarts the
# stack (and may roll the database back), so this waits for it.

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
dest=${RC_BACKUP_DIR:-}
if [[ -z $dest ]]; then
  echo "backup: set RC_BACKUP_DIR in ops/.env (the NAS folder backups go to)" >&2
  exit 2
fi
# The folder is made once at install; missing means the share isn't mounted, and writing
# would fill the mount point on the local disk instead.
if [[ ! -d $dest ]]; then
  echo "backup: $dest doesn't exist; is the NAS share mounted?" >&2
  exit 1
fi
mkdir -p "$state"
compose=(docker compose -f "$here/compose.yml")

if command -v flock >/dev/null; then
  exec 9>"$state/deploy.lock"
  if ! flock -w "${RC_LOCK_WAIT:-1800}" 9; then
    echo "backup: a deploy still holds $state/deploy.lock; not run" >&2
    exit 75
  fi
else
  echo "backup: flock not found; not guarding against a running deploy" >&2
fi

# After a reboot (the timer catches up on a missed night) the stack may still be starting.
tries=${RC_API_WAIT:-20}
until "${compose[@]}" exec -T api true >/dev/null 2>&1; do
  tries=$((tries - 1))
  if ((tries <= 0)); then
    echo "backup: the api container isn't running; not run" >&2
    exit 1
  fi
  sleep "${RC_API_WAIT_DELAY:-30}"
done

name=$("${compose[@]}" exec -T api python -m retro_cable.backup take --dest /data/backups)
pattern='^retro-cable-[0-9]{8}T[0-9]{6}Z\.sqlite\.gz$'
if [[ ! $name =~ $pattern ]]; then
  echo "backup: unexpected backup name from the api container: $name" >&2
  exit 1
fi
cp "$data/backups/$name" "$dest/.$name.part"
mv "$dest/.$name.part" "$dest/$name"
echo "backup: $name copied to $dest"

for f in "$dest"/retro-cable-*.sqlite.gz; do
  [[ -e $f ]] && echo "${f##*/}"
done | "${compose[@]}" exec -T api python -m retro_cable.backup expired | while read -r old; do
  # Only names the backup job makes, and only in this folder.
  if [[ $old =~ $pattern && -f $dest/$old ]]; then
    rm -f -- "${dest:?}/$old"
    echo "backup: removed $old (retention)"
  fi
done

# The restore test reads the NAS copy, the one a restore would start from.
"${compose[@]}" exec -T api python -m retro_cable.backup verify - --name "$name" <"$dest/$name"
