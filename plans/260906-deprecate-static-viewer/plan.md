---
title: "Decommission Static Browser Viewer"
description: "Remove the deprecated generated HTML viewer after the React/Electron cutover."
status: completed
priority: P1
tags: [react, electron, cleanup, deprecation]
created: 2026-09-06
---

# Decommission Static Browser Viewer

## Outcome

The React/Electron desktop app is the only supported viewer. The repository no longer generates or launches the deprecated browser HTML application.

## Constraints

- Preserve Python indexing, CSV output, state files, cleanup, organize, and enrichment behavior.
- Preserve the loopback JSON API consumed by Electron.
- Do not remove source data or unrelated existing work.
- Keep tests aligned with the new supported surface.

## Non-goals

- Rewriting Python indexing or mutation logic.
- Removing `assets.csv` generation or state artifacts used by tooling.
- Adding a browser compatibility shim.

## Changes

1. Remove the generated legacy HTML template and emitter from `bin/index_assets.py`.
2. Keep emit/update commands producing CSV, state, and review queue output.
3. Remove static HTML/CSV serving and emit-target coupling from `bin/server.py`; retain API serving.
4. Delete the root generated `index.html` and `Open Viewer.command` launcher.
5. Replace legacy HTML-generation and launcher tests with assertions for the React/Electron-supported surface.
6. Verify Python regressions, TypeScript build, and `make dev`.

## Acceptance

- No supported command or test depends on the deprecated browser viewer or launcher.
- `index_assets.py update/emit` still writes authoritative state, CSV, and review queue outputs.
- `/api/state` and `/api/assets` remain available to Electron.
- The React/Electron app starts through `make dev`.
- Full existing test suite and frontend build pass.
