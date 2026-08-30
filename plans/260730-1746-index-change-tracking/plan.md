---
title: "Index Change Tracking"
description: "One offline `update` command: rescan, diff against the previous state, append a changelog, queue newly-appeared assets for enrichment."
status: completed
priority: P2
effort: "0.5d"
tags: [unity, asset-management, tooling, tdd]
created: 2026-07-30
mode: tdd
follows: 260730-1154-unity-asset-index
brainstorm: ./brainstorm-summary.md
---

# Index Change Tracking

## Overview

The index built in `260730-1154-unity-asset-index` (complete: 861 files → 835 assets, 717 store-enriched) is a snapshot. This plan makes it answer "what changed?" when packages are added, replaced or removed.

One new offline command:

```bash
python3 .index/bin/index_assets.py update     # scan -> diff -> changelog -> queue -> emit
```

Design and empirical findings: [brainstorm-summary.md](./brainstorm-summary.md).

## Locked decisions

| # | Decision | Consequence |
|---|---|---|
| 1 | **Manual trigger only** — no scheduler | Index goes stale silently between runs → viewer must show `generated` |
| 2 | **Append-only `.index/CHANGES.md`** — no git | Zero corruption risk; no branching/blame (not wanted) |
| 3 | **Queue new assets, never auto-enrich** | New assets need a web search, which needs a session |
| 4 | **Single machine writes `.index/`** | No lock file, no cache merge |

## Binding constraints (carried from the predecessor, all measured)

| Constraint | Consequence |
|---|---|
| A full rescan is **0.09 s** for 861 files | No incremental scan, no file watching, no caching of scan results. Simplicity is free here |
| **`mtime` is untrustworthy** — AllSky: identical size, higher version, *older* mtime, because OneDrive rewrites timestamps | Diff keys on `(path, size)` only. A test asserts an mtime-only change yields zero diff entries |
| 845/852 archives are OneDrive dataless placeholders | `update` must never open an archive. The existing no-open test extends to this path |
| Cache is keyed by `asset_key` | A new *version* of a known asset reuses its resolution offline; a genuinely *new* asset cannot be enriched without a search |
| **`.git` inside this folder is unsafe** | OneDrive syncs `.git/index`, `objects/`, `refs/`; a mid-write sync corrupts the repo, and macOS OneDrive has no per-subfolder exclusion. If git is ever wanted, the repo lives *outside* the folder |

## Goals

| # | Goal | Priority |
|---|------|----------|
| 1 | `update` reports added / removed / resized files correctly | P1 |
| 2 | Distinguish a new *version of a known asset* (automatic) from a genuinely *new asset* (needs search) | P1 |
| 3 | Append a readable history to `.index/CHANGES.md` | P1 |
| 4 | Queue new assets to `.index/pending-enrichment.json` and make them visible | P2 |
| 5 | Make index staleness visible in the viewer | P2 |

## Architecture

```
update:
  prev = {rel_path: size_bytes}   read from existing .index/assets.json  (it IS the previous state)
  now  = scan(root)               0.09s, os.walk + os.stat only
  diff on (path, size) — mtime deliberately unused
      added   -> parse -> asset_key -> cache lookup
                     resolved in cache  -> NEW VERSION of known asset (automatic)
                     absent from cache  -> NEW ASSET -> pending-enrichment.json
                     present but failed -> NEW ASSET, marked "previously unresolved"
      removed -> version disappears; cache entry retained (a re-download resolves instantly)
      resized -> same path, different bytes: replaced in place without a version bump. Report only
  append .index/CHANGES.md   (newest first)
  write  .index/pending-enrichment.json
  emit   index.html + assets.csv + review-queue.md
```

**No new manifest artifact.** `assets.json` already carries `(rel_path, size_bytes)` for every file, so it is the previous-state record — read before overwrite. DRY: one fewer file to keep consistent.

### New functions (all in `index_assets.py`)

| Function | Signature | Pure? |
|---|---|---|
| `manifest_of(data)` | `assets.json dict -> {rel_path: size}` | yes |
| `diff_manifest(prev, now)` | `-> {added, removed, resized}` | yes |
| `classify_added(paths, cache)` | `-> {new_assets, new_versions}` | yes |
| `format_changelog_entry(diff, classified, when)` | `-> str` | yes |
| `append_changelog(path, entry)` | prepends under the header | I/O |

Everything except `append_changelog` is a pure function over dicts — directly unit-testable with no filesystem.

## Phases

| # | Phase | Status |
|---|-------|--------|
| 1 | [Phase 1: Diff, Changelog and Queue](./phase-01-diff-changelog-and-queue.md) | **Completed** |
| 2 | [Phase 2: Surface in Viewer and Enrichment](./phase-02-surface-in-viewer-and-enrichment.md) | **Completed** |

Phase 1 delivers the working command. Phase 2 makes its output visible and consumable.

## TDD contract

`--tdd` mode. This modifies `index_assets.py`, which 130 passing tests already cover, so existing behaviour is locked first. Four tests carry the weight:

| Test | Guards |
|---|---|
| An mtime-only change produces **zero** diff entries | The AllSky failure mode — OneDrive rewrites timestamps, so trusting mtime invents phantom changes |
| A rename whose new filename normalizes to the same `asset_key` is a **known asset**, not queued | The common "downloaded v2.19 over v2.18" case wasting a search |
| `update` twice in a row writes **one** changelog entry | Idempotency — the same class of bug that corrupted 213 `local_name` records in the predecessor |
| `update` opens zero archives | Hydrating 294 GB |

## Explicitly out of scope

- Scheduler (launchd timer / WatchPaths / fswatch) — one plist away if wanted later
- Git, in any form, inside or outside the folder
- Lock file, cache merge, multi-machine coordination
- macOS notifications
- Auto-enrichment of new assets
- Content hashing (would hydrate up to 294 GB)

## Success Criteria

- [x] Add an archive → `update` → changelog lists it; queue contains its `asset_key`; viewer shows it
- [x] Add a *new version* of a known asset → changelog says resolution reused; queue does **not** contain it
- [x] Delete an archive → changelog lists the removal; its cache entry survives
- [x] Replace an archive in place with different bytes → reported as `resized`, not re-resolved
- [x] Rename to a name normalizing to the same `asset_key` → treated as a known asset, no queue entry
- [x] An mtime-only change → **zero** diff entries
- [x] `update` run twice → second run appends no changelog entry
- [x] `update` opens zero archives; materialized path-set unchanged
- [x] Missing `assets.json` → prints "no previous state, establishing baseline", writes no changelog entry
- [x] Viewer header shows when the index was generated
- [x] `export-titles --pending` emits only queued assets
- [x] Full suite green (130 tests today, more after)

## Open questions

Carried from [brainstorm-summary.md](./brainstorm-summary.md). None blocks implementation.

| # | Question | Default if unanswered |
|---|---|---|
| 1 | Changelog retention — unbounded, or trim past N entries/months? | Unbounded. Revisit at ~500 entries |
| 2 | Should `resized` flag for manual review, or report only? | Report only. Detecting a real content change needs hashing, which hydrates |
| 3 | Should `pending-enrichment.json` entries expire? | Accumulate with `first_seen`. Expiry risks silently forgetting an asset |

## Implementation notes — Phase 1 complete

`update` verb live. **162 tests passing** (was 130). Real library: 861 files, 835 assets,
717 id-verified, enrichment intact, zero hydration.

### Two data-destroying bugs, both caught by watching a number rather than by a test

**`update` wiped all enrichment.** `build()` regenerates entries from disk with empty store
metadata, and `update` wrote that straight out — dropping id-verified from 717 to 0. The
documented pipeline is scan → **enrich** → emit and I skipped the middle step. Caught only
because the post-run summary printed `0 id-verified`. Fixed by running the enrichment merge
before the write; regression test asserts a cached resolution survives `update`.

**`scan update` in one invocation re-wiped it.** The `update` block wrote merged data, then
the standalone `scan` block rebuilt from disk and overwrote it. Reachable via
`scan emit update` — the natural extension for anyone used to `scan emit`, which is the only
form the module docstring shows. Found by code review, not by me. `update` now strips
`scan` from the step list; the regression test exercises four step permutations.

### A test-infrastructure bug that was hiding coverage in both files

`if __name__ == "__main__": unittest.main()` sat **mid-file** in both test modules, because
classes were appended after it across several sessions. Direct invocation reported a clean
"OK" while silently skipping every class below the guard:

| File | Direct run before | After |
|---|---|---|
| `test_index_assets.py` | 37 tests | 81 |
| `test_resolve_store.py` | 42 tests | 81 |

Discovery (`unittest discover`) was never affected, which is why it went unnoticed. The
skipped set included the 717-to-0 regression test itself. A test now asserts the guard is
the last statement in both files.

### Other review findings fixed

| Severity | Finding |
|---|---|
| High | `append_changelog` sliced by `len(CHANGELOG_HEADER)`, silently destroying all history the moment the header text changed. Now locates the body by its first `## ` marker, and handles a headerless file |
| High | `write_pending_queue` silently discarded every entry on a malformed file — the exact failure the accumulate-with-`first_seen` design exists to prevent. Now preserves it as `.corrupt` and warns |
| Medium | `assets.json` read crashed on malformed JSON while its two sibling reads degraded gracefully. Now consistent |
| Low | `is_baseline` conflated "no previous state" with "previous state was legitimately empty". Now an explicit `had_previous` flag |

### Verified live

Round-trips used a **0-byte placeholder** named `*.unitypackage`, never a copy of a real
archive — copying one would hydrate it. Confirmed: baseline writes no changelog; second run
idempotent; added file queued; `DunGen v9.9.9` recognised as a new *version* and NOT queued;
resized reports the byte delta; removal logged; entries newest-first. All test artifacts
removed, materialized path-set unchanged at 9.

## Implementation notes — Phase 2 complete

**179 tests passing** (130 before this plan, 162 after Phase 1). Real library unchanged:
861 files, 835 assets, 717 id-verified, materialized path-set still 9.

Shipped:
- **Staleness stamp** — header reads `indexed today` / `indexed N days ago`, with the raw
  UTC timestamp on hover and a muted-warning colour past 7 days. This is the entire
  mitigation for choosing a manual trigger: without it a stale index is indistinguishable
  from a current one.
- **`pending-enrichment` flag** — annotated at *emit* time, not build time. `update` writes
  the queue after scanning, so annotating in `build()` would land the flag a run late. It
  joins `flagsOf()`, so the facet sidebar, badge and filter all work with no extra code.
- **`export-titles --pending`** — 826 store-eligible without the flag, only the queued
  assets with it.
- **Queue pruning in `enrich`** — a resolved entry leaves the queue; an unresolved one
  stays, because dropping it would silently forget an asset.

Verified end-to-end with a 0-byte placeholder (never a copy of a real archive): queued →
flagged in the viewer → listed by `--pending` → pruned by `enrich` once resolved → removed.
Cache was backed up before injecting the test resolution and restored byte-for-byte after.

Both test files now run their full suite on direct invocation (89 and 90) as well as via
discovery (179) — the mid-file `unittest.main()` guard fixed in Phase 1 had been hiding
roughly half of each file.

<!-- slug: index-change-tracking -->
