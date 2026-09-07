---
title: "React Electron Python Desktop Migration"
description: "Replace the generated browser viewer with a packaged React renderer while preserving the guarded Python asset actions."
status: completed
priority: P1
tags: [unity, assets, react, electron, python, desktop]
created: 2026-09-05
---

# React Electron Python Desktop Migration

## Outcome

A user launches one macOS desktop app. Electron starts or reuses the loopback Python backend, React renders the asset library, and Resync, Organize, and Enrich remain available without a terminal or command file.

### Resync review update — 2026-09-06

- Resync now calls read-only `POST /api/resync/plan`, listing every added, removed, and resized vault-relative path before updating the index.
- Apply calls `POST /api/resync/apply` with the reviewed hash; vault or index-manifest drift requires a fresh review. The index writer consumes the validated scan.
- Cancel leaves index data untouched. Index removal never deletes archives; disk cleanup remains a separate, explicit confirmation after sync.
- Restart Electron and its Python backend after updating; the old immediate-mutation `/api/resync` route is removed.
- Verification: real temporary-vault HTTP apply, full Python suite, TypeScript/Vite build, and browser-driven renderer review/cancel/apply/drift/empty-state checks.

### Gluestack renderer integration

- Keep the existing Electron/Vite renderer and dense dark/coral workbench; do not introduce Next.js, Expo, or a second styling system.
- Use the pinned gluestack v4 core factories through the web-only host boundary in `app/src/components/ui/index.tsx`. The version choice preserves web support; reassess its alpha API before upgrading.
- Use its shared gluestack `Icon` with named Lucide imports instead of hand-maintained SVG paths or CSS icon glyphs; keep prose arrows as text. This preserves a consistent glyph vocabulary without introducing icon fonts or network assets.
- `vite.config.ts` owns web-variant resolution and `app/src/main.tsx` owns the shared overlay provider. Both are required: native variants or duplicate provider contexts can leave overlays blank.
- Dialog restoration follows actual portal detachment, not owner unmount; keep the persistent action trigger as the return target because menu items disappear before confirmation opens.
- Verification: packaged Electron rendering; fixture-backed search/filter/sort, preview/apply hashes, busy guards, error/retry, enrichment cancellation, keyboard containment and focus restoration; Python regression suite and desktop bootstrap checks.
- Verification limit: disposable-backend cleanup apply exposed an existing state-lock hang after deletion. Cleanup/organize UI applies were checked with a responding bridge; their real-backend previews were checked without claiming successful filesystem applies.

## Constraints

- Preserve the Python backend's fail-closed filesystem safety and preview/confirm/apply contracts.
- Keep the backend loopback-only and expose React through a narrow preload bridge.
- Use the authoritative `state/assets.json` through a fixed read-only API endpoint.
- Target macOS first; keep the backend implementation portable.
- Do not expose Node or arbitrary filesystem access to the renderer.

## Non-goals

- Rewriting the Python engine into TypeScript.
- Cloud sync, accounts, telemetry, or remote collaboration.
- Replacing the existing organizer, resolver, or cleanup algorithms.
- Building a cross-platform installer in this slice.

## Design

- Electron main process owns backend startup, process cleanup, IPC handlers, and safe external-link opening.
- Packaged runs copy the backend into one writable `app.getPath("userData")` workspace; generated state/output never target the read-only app bundle.
- Backend SIGTERM is graceful: the server drains active enrichment work, releases locks, and Electron waits before escalating to the owned process group.
- Preload exposes only named backend operations.
- React uses a three-zone workbench: filter rail, asset canvas, and detail inspector.
- High-value convenience features are persistent view/sort preferences, quick health filters, library summary, keyboard-selectable controls, and explicit loading/error/action states.
- Visual language follows Emil design-engineering principles: restrained motion, responsive press states, strong hierarchy, and no decorative animation on frequent interactions.

## Delivery phases

1. Add `/api/assets` and its contract test.
2. Scaffold Vite React and Electron packaging.
3. Start/reuse the Python backend from Electron and expose typed IPC operations.
4. Port the library workflow into the redesigned React renderer.
5. Build, package, run backend regression tests, and smoke the real `.app`.

## Acceptance

- `npm run build` typechecks and bundles the renderer with relative asset paths suitable for `loadFile()`.
- `npm run package` creates an arm64 macOS app bundle.
- The packaged app starts the Python backend without a terminal and `/api/state` reports the expected service identity.
- The React renderer displays authoritative assets, filters, selection details, and action controls.
- Action calls retain preview-before-confirm behavior and surface backend failures.
- Existing Python tests plus frontend/build checks are green.
