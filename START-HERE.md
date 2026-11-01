# Start here

This folder is the whole Retro Cable project so far, ready to become the `btabaska/retro-cable` repo.

## What's inside

| Path | What it is |
| --- | --- |
| `docs/plan.md` | The approved build plan (architecture, scheduler, phases, costs) |
| `docs/archive-list.md` | Shows, movies and bumpers to archive, per channel, to prune |
| `docs/mockups/` | Screen mockups: TV guide, live TV, year picker, and the phone admin (`.html` to open, `.png` to glance) |
| `docs/phase-prompts.md` | The prompt to paste into Claude Code for each phase |
| `docs/STATUS.md` | Current phase and the one-time setup checklist |
| `CLAUDE.md` | Rules every agent follows in this repo |
| `catalog/` | Starter channel lineup and holiday calendar |

## Continue in three steps

1. Copy everything in this folder into your local clone of `btabaska/retro-cable`, then commit and push to `main` (the only direct push; agents use PRs after this).
2. Work through the Phase 0 checklist in `docs/STATUS.md`.
3. Open Claude Code (on the web or with `claude` in this folder on your Mac) and paste the Phase 1 prompt from `docs/phase-prompts.md`.

One question is still open: which always-on homelab machine runs the stack. Phase 1 can start without it; the first deploy needs it.
