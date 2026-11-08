"""The channel builder (Phase 11): `python -m retro_cable.channel_builder JOB [options]`.

  propose  draft channels for a show list CSV with the local model; writes proposal.json,
           proposal.md and proposal.csv into --out. Saves nothing.
  check    run the /admin checks on a proposal against the current database; saves nothing.
  apply    check a proposal and save it as one /admin overlay revision (undoable). With
           --shows, TMDB ids and years missing from an older proposal come from the list.

The model is reached through RC_LLM_URL, RC_LLM_KEY and RC_LLM_MODEL (channel_builder/llm.py).
"""

import argparse
import os
import sqlite3
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from retro_cable.admin import store
from retro_cable.admin.drafts import LibraryItem
from retro_cable.admin.router import library_index
from retro_cable.catalog import Catalog, CatalogError, load_catalog
from retro_cable.catalog.overlay import Overlay, apply_overlay
from retro_cable.channel_builder.build import build_proposal
from retro_cable.channel_builder.llm import LLM, LLMError, OpenAIChat, ReplyLog
from retro_cable.channel_builder.proposal import (
    Checked,
    NotApplied,
    Proposal,
    apply_proposal,
    check_proposal,
    fill_from_list,
)
from retro_cable.channel_builder.report import to_csv, to_markdown
from retro_cable.channel_builder.shows import ShowRow, known_ids, read_shows
from retro_cable.db import connect, migrate
from retro_cable.scheduler import Library, Scheduler, load_library


def default_db() -> Path:
    data = Path(os.environ.get("RC_DATA_DIR", "data"))
    return Path(os.environ.get("RC_DB", data / "retro-cable.db"))


@dataclass
class Context:
    base: Catalog
    head: Overlay
    library: Library
    index: dict[str, LibraryItem]
    seed: int

    @property
    def effective(self) -> Catalog:
        return apply_overlay(self.base, self.head)


def load_context(db: sqlite3.Connection | None, catalog_dir: Path, seed: int) -> Context:
    base = load_catalog(catalog_dir)
    if db is None:
        return Context(base, Overlay(), Library(), {}, seed)
    head = store.head(db).overlay
    library = load_library(db)
    sched = Scheduler(apply_overlay(base, head), library, seed=seed)
    return Context(base, head, library, library_index(db, sched), seed)


def spread(rows: Sequence[ShowRow], n: int) -> list[ShowRow]:
    """`n` rows spread evenly over the list, so every category is represented."""
    if n <= 0 or n >= len(rows):
        return list(rows)
    step = len(rows) / n
    return [rows[int(i * step)] for i in range(n)]


def attach(proposal: Proposal, checked: Checked) -> Proposal:
    return proposal.model_copy(update={"issues": checked.issues})


def write(proposal: Proposal, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "proposal.json").write_text(proposal.model_dump_json(indent=2) + "\n")
    (out / "proposal.md").write_text(to_markdown(proposal))
    (out / "proposal.csv").write_text(to_csv(proposal))


def summary(checked: Checked) -> str:
    errors = sum(1 for i in checked.issues if i.severity == "error")
    warnings = len(checked.issues) - errors
    return (
        f"{len(checked.applied)} channels pass, {len(checked.skipped)} fail"
        f" ({errors} errors, {warnings} warnings)"
    )


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m retro_cable.channel_builder")
    p.add_argument("job", choices=["propose", "check", "apply"])
    p.add_argument("proposal", nargs="?", type=Path, help="proposal.json (check, apply)")
    p.add_argument("--db", type=Path, default=None, help="default RC_DB or RC_DATA_DIR")
    p.add_argument(
        "--catalog", type=Path, default=Path(os.environ.get("RC_CATALOG_DIR", "catalog"))
    )
    p.add_argument("--seed", type=int, default=int(os.environ.get("RC_SEED", "0")))
    p.add_argument(
        "--shows", type=Path, help="the show list CSV (propose; apply: fill missing TMDB ids)"
    )
    p.add_argument("--out", type=Path, help="folder for proposal.json/.md/.csv (propose)")
    p.add_argument("--sample", type=int, default=0, help="only N shows, spread over the list")
    p.add_argument("--on-nas-only", action="store_true", help="only shows marked on_nas")
    p.add_argument("--batch", type=int, default=40, help="shows per placement prompt")
    p.add_argument(
        "--resume", action="store_true", help="propose: reuse the replies a cut-short run saved"
    )
    p.add_argument("--skip-invalid", action="store_true", help="apply: leave out failing channels")
    return p


def main(
    argv: list[str] | None = None,
    llm: LLM | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    args = _parser().parse_args(argv)
    db_path = args.db or default_db()
    db: sqlite3.Connection | None = None
    if db_path.exists() or args.job == "apply":
        db = connect(db_path)
        migrate(db)
    try:
        ctx = load_context(db, args.catalog, args.seed)
    except CatalogError as e:
        print(e, file=sys.stderr)
        return 1
    try:
        if args.job == "propose":
            return _propose(args, ctx, llm or OpenAIChat.from_env(), clock)
        if args.proposal is None:
            print("give the proposal.json to check or apply", file=sys.stderr)
            return 2
        proposal = Proposal.model_validate_json(args.proposal.read_text())
        if args.shows is not None:
            proposal = fill_from_list(proposal, _rows(args.shows, ctx))
        if proposal.base_version != ctx.effective.version:
            print(
                f"note: drafted against catalog {proposal.base_version}, now"
                f" {ctx.effective.version}; the checks run against now",
                file=sys.stderr,
            )
        if args.job == "check":
            checked = check_proposal(proposal, ctx.base, ctx.head, ctx.index, ctx.library, ctx.seed)
            _print_issues(checked)
            print(summary(checked))
            return 0 if checked.ok else 1
        assert db is not None
        try:
            rev, checked = apply_proposal(
                db, proposal, ctx.base, ctx.index, ctx.library, ctx.seed, clock(),
                skip_invalid=args.skip_invalid,
            )  # fmt: skip
        except NotApplied as e:
            _print_issues(e.checked)
            print(f"not applied: {summary(e.checked)}", file=sys.stderr)
            return 1
        _print_issues(checked)
        print(f"applied as /admin revision {rev.id}: {summary(checked)}; undo in /admin")
        return 0
    finally:
        if db is not None:
            db.close()


def _print_issues(checked: Checked) -> None:
    for i in checked.issues:
        if i.severity == "error":
            print(f"error: {i.channel or ''}: {i.message}", file=sys.stderr)


def _rows(path: Path, ctx: Context) -> list[ShowRow]:
    """The list's kept rows, with ids resolved against the effective catalog and library."""
    catalog = ctx.effective
    titles = {c.show: c.title for c in catalog.carriage.carriage}
    by_tmdb = {o.tmdb_id: o.show for o in catalog.overrides.shows if o.tmdb_id and o.show in titles}
    known = known_ids(titles, {i.id: i.title for i in ctx.index.values()})
    return read_shows(path, known, by_tmdb)


def _propose(
    args: argparse.Namespace, ctx: Context, llm: LLM, clock: Callable[[], datetime]
) -> int:
    if args.shows is None or args.out is None:
        print("propose needs --shows CSV and --out DIR", file=sys.stderr)
        return 2
    catalog = ctx.effective
    rows = _rows(args.shows, ctx)
    if args.on_nas_only:
        rows = [r for r in rows if r.on_nas or (r.id in ctx.index and ctx.index[r.id].episodes)]
    rows = spread(rows, args.sample)
    log = ReplyLog(llm, args.out / "replies.jsonl", replay=args.resume)
    try:
        proposal = build_proposal(
            rows, catalog, ctx.index, log, clock(), args.shows.name, batch_size=args.batch
        )
    except LLMError as e:
        print(f"model: {e}", file=sys.stderr)
        print("the replies so far are saved; run again with --resume to pick up from here",
              file=sys.stderr)  # fmt: skip
        return 1
    if log.replayed:
        print(f"resumed: {log.replayed} replies from the last run")
    checked = check_proposal(proposal, ctx.base, ctx.head, ctx.index, ctx.library, ctx.seed)
    proposal = attach(proposal, checked)
    write(proposal, args.out)
    print(f"{len(rows)} shows -> {len(proposal.channels)} channels; {summary(checked)}")
    print(f"wrote {args.out / 'proposal.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
