"""The weekly station manager (M5): `python -m retro_cable.station_manager JOB [options]`.

  run    read the last --weeks of viewing, ask the local model for a diff, keep what the
         guardrails and the /admin checks allow, and apply it as one /admin revision
         (RC_STATION_AUTO_APPLY=1, the default) or record it as a proposal (0). With
         --dry-run nothing is applied either way. Every run is recorded.
  apply  apply a proposed or dry run by its id, if the weights haven't changed since.

On the mini a systemd timer runs `run` every Sunday at 2 a.m. inside the api container
(ops/station-manager.sh). The model is reached through RC_LLM_URL, RC_LLM_KEY and
RC_LLM_MODEL (channel_builder/llm.py); never a cloud model.
"""

import argparse
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from retro_cable.admin import store
from retro_cable.catalog import CatalogError, load_catalog
from retro_cable.channel_builder.__main__ import default_db
from retro_cable.channel_builder.llm import LLM, OpenAIChat
from retro_cable.db import connect, migrate
from retro_cable.scheduler import load_library
from retro_cable.station_manager.manager import (
    NotApplied,
    Options,
    apply_run,
    auto_apply,
    run,
)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m retro_cable.station_manager")
    p.add_argument("job", choices=["run", "apply"])
    p.add_argument("run_id", nargs="?", type=int, help="apply: the run to apply")
    p.add_argument("--db", type=Path, default=None, help="default RC_DB or RC_DATA_DIR")
    p.add_argument(
        "--catalog", type=Path, default=Path(os.environ.get("RC_CATALOG_DIR", "catalog"))
    )
    p.add_argument("--seed", type=int, default=int(os.environ.get("RC_SEED", "0")))
    p.add_argument("--weeks", type=int, default=4, help="weeks of viewing to read (default 4)")
    p.add_argument("--dry-run", action="store_true", help="propose and record, apply nothing")
    return p


def main(
    argv: list[str] | None = None,
    llm: LLM | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    args = _parser().parse_args(argv)
    if not 1 <= args.weeks <= 52:
        print("--weeks runs from 1 to 52", file=sys.stderr)
        return 2
    try:
        base = load_catalog(args.catalog)
    except CatalogError as e:
        print(e, file=sys.stderr)
        return 1
    db = connect(args.db or default_db())
    try:
        migrate(db)
        library = load_library(db, allow_short=os.environ.get("RC_ALLOW_SHORT_MEDIA") == "1")
        if args.job == "apply":
            if args.run_id is None:
                print("give the run id to apply", file=sys.stderr)
                return 2
            try:
                rev = apply_run(db, base, library, args.run_id, clock(), args.seed)
            except (NotApplied, KeyError, store.Conflict) as e:
                print(f"not applied: {e}", file=sys.stderr)
                return 1
            print(f"applied run {args.run_id} as /admin revision {rev.id}; undo in /admin")
            return 0
        opts = Options(
            weeks=args.weeks, auto_apply=auto_apply(), dry_run=args.dry_run, seed=args.seed
        )
        done = run(db, base, library, llm or OpenAIChat.from_env(), clock(), opts)
        print(f"station manager run {done.id} ({done.status}): {done.summary}")
        for c in done.report.changes:
            where = f" {c.template} {c.start}" if c.template else ""
            print(f"  {c.channel}{where} {c.show}: x{c.before:g} -> x{c.after:g}  {c.why}")
        for ph in done.report.power_hours:
            print(f"  power hour {ph.start} ({ph.hours} h) on {ph.channel}: {', '.join(ph.shows)}")
        for d in done.report.dropped:
            print(f"  dropped {d.what}{f' on {d.channel}' if d.channel else ''}: {d.reason}")
        return 1 if done.status == "failed" else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
