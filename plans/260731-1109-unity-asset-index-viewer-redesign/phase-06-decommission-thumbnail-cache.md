---
phase: 6
title: "Decommission Thumbnail Cache"
status: completed
priority: P2
effort: "0.5h"
dependencies: [2, 3, 4, 5]
---

# Phase 6: Decommission Thumbnail Cache

<!-- Updated: Validation Session 1 - phase created from the decision to delete .index/thumbs -->

## Overview

Remove the now-unread local thumbnail mirror: 708 files, 303 MB, plus its OneDrive-synced
copy. Strip the dangling `thumbnail.local` references so nothing points at deleted files.

**Deliberately last.** Deletion is the one irreversible step in this plan. Recovery costs 708
rate-limited web fetches, and the transports rate-limit hard (Brave returned 429 at request 5
during the original enrichment run). The cache stays on disk as a manual escape hatch through
Phases 1-5 so that if the remote path turns out to be wrong, nothing has been lost yet.

## Requirements

**Functional**
- `.index/thumbs/` removed
- `build()` no longer emits `thumbnail.local` into `assets.json`
- `assets.json` regenerated so no entry references a deleted path
- Cache entries' `thumbnail_local` keys removed or ignored, so a later `enrich` cannot
  resurrect a dangling path

**Non-functional**
- Deletion happens only after Phases 2-5 are verified working, including the airplane-mode check
- **Explicit user confirmation at the moment of deletion.** Do not delete as a side effect of
  running the phase

## Architecture

### Order matters

```
1. verify phases 2-5 green and the viewer works online + offline
2. strip thumbnail.local from build() output
3. strip thumbnail_local from cache.json entries
4. regenerate assets.json + index.html, re-verify
5. ONLY THEN: confirm with the user, delete .index/thumbs/
```

Deleting first and cleaning references after would leave a window where `assets.json` points at
files that no longer exist. Nothing reads `thumbnail.local` after Phase 2, so the window is
harmless in practice, but the ordering above costs nothing and keeps every intermediate state
coherent.

### What is NOT deleted

`mirror_thumbnail` in `resolve_store.py` and its ~8 tests stay. Phase 2 records why: it is the
reversal path, and it carries host-allowlist and scheme-rejection coverage that would be
expensive to rebuild. An unreferenced-but-tested helper is the intended end state here.

### Deletion mechanics

`.index/thumbs/` is a cache directory outside `Assets/`, so the Unity asset guard does not
apply. Still, prefer an explicit `rm -rf` on the exact path over anything glob-expanded, and
print the file count and byte total first so the confirmation is informed.

OneDrive reclaims the cloud copy asynchronously after the local delete syncs. The 303 MB local
figure understates the total saving.

## Related Code Files

- Modify: `.index/bin/index_assets.py` - stop emitting `thumbnail.local` in `build()`
- Modify: `.index/bin/tests/test_index_assets.py` - add `TestThumbnailLocalRemoved`
- Delete: `.index/thumbs/` (708 files, 303 MB) after confirmation
- Regenerate: `.index/assets.json`, `index.html`

## Tests First

1. `test_build_emits_no_local_thumbnail_path` - a built asset's `thumbnail` dict has `remote`
   and no `local` key
2. `test_no_thumbs_path_in_generated_html` - the string `.index/thumbs` does not appear in
   `HTML_TEMPLATE` or in a generated `index.html`

### Regression guards

- `TestBuildDeterminism` (414) must stay green. Removing a key from the output is fine as long
  as it is removed deterministically
- `test_resolve_store.py` fixtures at lines 315-333 contain `thumbnail_local` values. Check
  whether dropping the key from `build()` breaks any assertion there before changing it

## Implementation Steps

1. Confirm Phases 2-5 are complete and their success criteria all checked, including the
   airplane-mode check
2. Write the two tests. Confirm they fail
3. Remove `thumbnail.local` from `build()` output
4. Strip `thumbnail_local` from `cache.json` entries, or make the reader ignore it
5. Run the full suite
6. Regenerate `assets.json` and `index.html`. Re-verify the viewer online and offline
7. Report the exact file count and byte total to the user
8. **Ask for explicit confirmation, then** delete `.index/thumbs/`
9. Confirm the viewer still works after deletion

## Success Criteria

- [ ] Two new tests pass
- [ ] `TestBuildDeterminism` still green
- [ ] `assets.json` contains no `thumbnail.local` key
- [ ] Viewer verified working before deletion, and again after
- [ ] User explicitly confirmed the deletion
- [ ] `.index/thumbs/` gone, ~303 MB reclaimed locally plus the OneDrive copy
- [ ] A subsequent `scan` and `enrich` run recreates nothing under `.index/thumbs/`

## Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| **Deletion is irreversible at 708 rate-limited fetches** | High | Gated behind every other phase passing, plus explicit confirmation. Do not run this phase early |
| Regret after CDN rot shows up months later | Medium | Accepted trade, recorded here and in Phase 2. `mirror_thumbnail` is retained precisely so re-mirroring stays possible |
| Dropping `thumbnail.local` breaks a `resolve_store` fixture | Medium | Step 5 runs the full suite. Fixtures at `test_resolve_store.py:315-333` reference the key; check before changing |
| Deleting while a scan is mid-write | Low | Scan is 0.09 s and manual. Do not run concurrently |
| OneDrive resurrects files from cloud after local delete | Low | Verify the folder stays empty after sync settles, not just immediately after `rm` |
