---
phase: 2
title: "Phase 2: Surface in Viewer and Enrichment"
status: todo
priority: P2
effort: "2h"
dependencies: [1]
---

# Phase 2: Surface in Viewer and Enrichment

## Overview

Phase 1's queue and staleness information exist but are invisible. This phase surfaces both: a `generated` stamp in the viewer header, a `pending-enrichment` filter chip, and a `--pending` flag so enrichment can consume the queue directly.

## Requirements

**Functional**
- Viewer header shows when the index was last generated
- Assets in `pending-enrichment.json` carry a `pending-enrichment` flag, filterable via the existing flag facet
- `resolve_store.py export-titles --pending` emits a worklist of only queued assets
- `resolve_store.py enrich` clears entries from the queue once resolved

**Non-functional**
- No new external dependency; viewer stays self-contained with CSP intact
- Staleness stamp must not make `assets.json` non-deterministic in a way that breaks the existing determinism test

## Architecture

### Staleness stamp

`assets.json` already carries `generated` (UTC ISO-8601). The viewer renders it in the header, plus a relative age ("3 days ago") computed client-side.

Manual triggering was the chosen design, so a stale index is the expected failure mode — this makes it legible instead of silent. Age over ~7 days gets the muted-warning colour already used for `.pending`.

**Determinism caution:** `TestBuildDeterminism` compares two `build()` outputs after popping `generated`. Rendering `generated` in the viewer does not change that, but do not thread it into anything the test compares.

### Pending flag

`build()` reads `.index/pending-enrichment.json` when present and sets `pending_enrichment: true` on matching assets. `flagsOf()` in the viewer already collects flags for the facet sidebar and card badges, so adding one string participates in filtering and counting for free.

Ordering note: `build()` runs during `scan`, and Phase 1's `update` writes the queue *after* scanning. So the flag lands on the *following* run unless `update` re-runs `build` post-queue. Simplest correct fix: `update` writes the queue, then calls `emit`, and `emit` reads the queue directly when annotating — keeping the queue read in one place rather than duplicating it in `build`.

### `--pending`

```bash
python3 .index/bin/resolve_store.py export-titles --pending    # queued assets only
```

Filters the worklist to `asset_key`s present in the queue. Reuses the existing non-store exclusion, so a queued non-store asset (shouldn't happen, but) is still skipped.

### Queue lifecycle

`enrich` removes an `asset_key` from the queue once its cache record reaches `status == "resolved"`. Entries otherwise accumulate with `first_seen` — per open question 3, expiry risks silently forgetting an asset.

## Related Code Files

- Modify: `.index/bin/index_assets.py` — header stamp, `pending_enrichment` annotation in `emit`
- Modify: `.index/bin/resolve_store.py` — `--pending` flag, queue cleanup in `enrich`
- Modify: `.index/bin/tests/test_index_assets.py`, `.index/bin/tests/test_resolve_store.py`

## Implementation Steps

### 1. Write viewer tests, then implement the stamp

- `HTML_TEMPLATE` contains a `generated` element and reads `DATA.generated`
- Existing guards still hold: no `.innerHTML`, no external resource loads, CSP meta present
- `TestBuildDeterminism` still passes

### 2. Write pending-flag tests, then implement

- An asset in the queue emits with `pending_enrichment: true`; one absent does not
- The flag reaches `flagsOf()` so it appears in the facet sidebar
- Missing or empty queue file is a no-op, not a crash
- Malformed queue JSON warns and is ignored rather than failing `emit`

### 3. Write `--pending` tests, then implement

- With a 3-entry queue, `export-titles --pending` emits exactly those 3
- Without `--pending`, behaviour is unchanged (all store-eligible)
- Empty queue → empty worklist plus a clear message, not an error

### 4. Write queue-cleanup tests, then implement

- `enrich` drops an `asset_key` whose cache record is `resolved`
- An unresolved entry survives
- Cleanup is atomic; the queue is never left truncated

### 5. Verify against the real library

```bash
python3 .index/bin/index_assets.py update
open index.html          # header shows the generation time
```

Round-trip with a **0-byte placeholder** named `*.unitypackage` (never a copy of a real archive — that would hydrate it): `update` → confirm the badge and facet chip appear → `export-titles --pending` lists only it → delete → `update` → confirm it is gone.

## Success Criteria

- [x] Viewer header shows the generation time; age over ~7 days is visually muted
- [x] Queued assets carry `pending_enrichment` and are filterable via the flag facet
- [x] `export-titles --pending` emits only queued assets; without the flag behaviour is unchanged
- [x] `enrich` removes resolved entries from the queue; unresolved ones survive
- [x] Missing / empty / malformed queue file never breaks `emit`
- [x] Viewer still self-contained: no external loads, no `.innerHTML`, CSP present
- [x] `TestBuildDeterminism` still green
- [x] Full suite green

## Risk Assessment

| Risk | Mitigation |
|---|---|
| Staleness stamp breaks the determinism test | `generated` is already popped before comparison; do not thread it into compared fields |
| Flag lands a run late (queue written after `build`) | `emit` reads the queue directly, so the annotation is current within the same `update` |
| Malformed queue JSON breaks `emit` | Warn and ignore; tested |
| Queue cleanup drops an unresolved entry | Only `status == "resolved"` is removed; tested both ways |
| Verification round-trip hydrates an archive | 0-byte placeholder file, never a copy of a real archive |
| Another viewer flag makes the sidebar noisy | Only one string added; `flagsOf()` already de-duplicates per asset |
