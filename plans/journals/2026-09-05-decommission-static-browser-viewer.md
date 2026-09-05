---
title: Decommission static browser viewer
date: 2026-09-05
summary: Removed the deprecated generated browser viewer and kept the React/Electron API-only workflow.
---

# Decommission static browser viewer

## What happened
The repository had completed the React/Electron migration but still retained the generated HTML viewer, its Open Viewer.command launcher, and server-side static routes. Removing only the artifacts exposed two stale dependencies: pending-queue annotation had been adjacent to the deleted HTML block, and the server still advertised --open.

## Decision
Make the React/Electron desktop app the only supported viewer. Remove the legacy HTML template/emitter, launcher, static routes, and browser flag. Preserve CSV/state generation, pending annotation, enrichment/organize/cleanup behavior, and the loopback JSON API.

## Verification
253 Python tests, TypeScript typecheck, frontend build, API-only loopback smoke, and make dev passed. Root/index.html/assets.csv routes now return 404; /api/state and /api/assets remain available.

## Next steps
No implementation follow-up. Commit was requested through the git-manager workflow.

> Historical work record — not durable authority. Prefer docs/specs/ADRs for current decisions.
