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

See "Assumptions to confirm" in `docs/plan.md`. Still blocking: which homelab host runs the stack.
