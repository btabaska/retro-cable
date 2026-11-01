# Status

**Current phase:** 1 — Skeleton

## Phase 1 — Skeleton

- [x] Repo layout, Makefile, server (`/health`, `/version`), web "hello" screen, fixture generator, ops files, workflows
- [x] `docs/specs/` split from `docs/plan.md`
- [ ] Hermes deploys it (needs the homelab host)
- [ ] Mac runner posts its first `local-test` report

## Phase 0 — Permits (Brandon)

- [x] Private repo `btabaska/retro-cable` created
- [ ] Repo connected to Claude Code on the web
- [ ] Claude Code GitHub app installed (for `@claude` mentions on PRs)
- [ ] Mac registered as a self-hosted runner with labels `self-hosted, macOS, retro-cable`
- [ ] Hermes Agent on the Mac (build and test), connected to LiteLLM and Telegram/Signal
- [ ] Hermes Agent on the homelab host (operations)
- [ ] Split-DNS record for `tv.home.tabaska.us`
- [ ] Caddyfile `import` line for `ops/caddy/retro-cable.caddy`

## Open assumptions

See "Assumptions to confirm" in `docs/plan.md`.

- Homelab host: **decided by Claude on 2026-11-01 (Brandon asked it to choose): the Mac mini** runs the stack and the NAS stores the media. Reasoning is in `docs/specs/ops.md` (Where it runs).
- The GHCR packages are private and the mini has no read token, so the mini builds images from its checkout (`RC_IMAGE_SOURCE=build`). To switch to pulling, create a classic PAT with only `read:packages` and run `docker login ghcr.io` on the mini.
