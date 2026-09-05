---
title: Planned viewer Resync + Organize actions
date: 2026-09-04
summary: "Brainstormed and planned two server-backed viewer actions: vault resync with conservative cleanup preview, and full-breadcrumb organize moves; fast-mode plan written and validated."
---

# Planned viewer Resync + Organize actions

## What happened
- Brainstorm (ak-brainstorm) captured the contract for two viewer buttons: Resync (rescan vault + prune superseded versions, keep latest) and Organize (move archives into full Asset Store breadcrumb folders, e.g. 3D/Props/Weapons). Two material decisions asked and answered by the user: resync source = vault rescan only (no Unity Hub download-folder import), organize depth = full breadcrumb (flattens ad-hoc publisher/asset folders).
- Key evidence: a static index.html cannot execute Python, so buttons need a loopback stdlib companion server (bin/server.py) exposing fixed JSON endpoints that reuse existing modules in-process. Feature 1's engine already exists (bin/cleanup_versions.py, plan->confirm->apply, fail-closed); feature 2 needs a new organize engine mirroring its safety shape. 829/844 assets already carry store category.levels in state/cache.json; 65 loose archives sit in vault root; ~15 files carry their version only in the parent folder name (they move as versionless; cleanup already preserves unparseable families).
- Fast-mode plan (ak-plan) written by planner subagent at plans/260904-1342-viewer-resync-organize: Phase 1 organize engine, Phase 2 loopback server (hash-bound 409 drift guard, single mutation gate, pending-only enrich job, additive resolve --pending flag), Phase 3 HTML_TEMPLATE wiring (disabled-by-default actions, file:// hint, accessible preview modals, poll-based Enrich).

## Decision
- Plan preview -> explicit confirm -> fresh server-side re-plan -> fail-closed apply -> one re-index is mandatory for every destructive step; confirmation hash binds normalized plan + lstat snapshot, drift returns 409 with a replacement plan.
- Never rename files; never hydrate OneDrive placeholders; non-store assets and _Quarantine/_Unresolved never move; destination collisions skip, never overwrite.

## Next steps
- Post-plan handoff: red-team (recommended; destructive vault ops), validate, or straight to /ak:cook plans/260904-1342-viewer-resync-organize/plan.md.

> Historical work record — not durable authority. Prefer docs/specs/ADRs for current decisions.
