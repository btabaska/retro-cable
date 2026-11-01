# Retro Cable

Bay Area cable TV as it was on today's date in any year from 1990 to 2010: a browser app (then an Apple TV app) and a deterministic scheduling server, running on a homelab.

The project is built end to end by agents: Claude Code in the cloud writes the code, a self-hosted runner and Hermes Agent on the Mac test it locally, and Hermes on the homelab deploys and maintains it.

## Start here

- `docs/plan.md` is the approved build plan.
- `CLAUDE.md` holds the rules every agent follows.
- `docs/STATUS.md` tracks the current phase and Phase 0 setup.
- `docs/phase-prompts.md` has the prompt to paste into Claude Code for each phase.

## Content

No real video belongs in this repo. Media lives on the NAS; tests use generated fixtures.
