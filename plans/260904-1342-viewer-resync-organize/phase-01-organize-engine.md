---
phase: 1
title: "Organize Engine"
status: completed
priority: P1
effort: "7h"
dependencies: []
---

# Phase 1: Organize Engine

## Overview

Create a deterministic planner and fail-closed apply engine for moving indexed store archives into full breadcrumb category folders. It preserves filenames, skips unsafe or ineligible work, rolls back reversible partial batches, and reuses the established vault safety shape.

## Requirements

**Functional**

- Add `plan_organize(root, data, scanned=None)` [NEW] returning sorted moves, category groups, skips, warnings, and totals without mutating disk or generated state.
- One move destination is `<vault>/<category.levels[0]>/.../<category.levels[n]>/<source basename>`; never retain ad-hoc source parents.
- Eligibility requires `non_store == false`, a non-empty category with `source == "store"`, and a live version path represented by the current metadata scan. Store categories are populated during merge at `bin/resolve_store.py:710-770`.
- Skip classes exposed to callers: `non-store`, `no-category`, `already-correct`, `blocked-ancestor`, and `collision`. A source under an excluded directory or an unsafe category/path is a hard safety error, not a move. _(RT#9)_
- Detect both an existing destination and two planned sources resolving to the same destination, keyed on NFC-normalized, case-folded destination paths (the target APFS volume is case-insensitive and normalization-insensitive). Skip the colliding moves; never choose a winner. _(RT#14)_
- Add a warning to every move whose version row has `folder_hint`; moving proceeds only after the higher layer's explicit confirmation. `folder_hint` is emitted on each version at `bin/index_assets.py:471-485`.
- Add `apply_organize(...)` [NEW]: fresh identity/destination preflight, no-clobber moves via atomically-failing `os.link` + `os.unlink` on the same volume (`os.rename` silently replaces an existing destination on POSIX), reverse completed moves on a mid-batch failure, remove newly empty source-parent chains, then call `index_assets.main(["update", ...])` once. _(RT#1)_
- Add CLI parity with cleanup: deterministic dry-run by default; `--apply` is the explicit mutation switch; `--root` / `--state` remain test overrides and preserve configured output routing.

**Non-functional**

- Stdlib only. No archive content reads, hashing, copy, download, or OneDrive hydration.
- Deterministic output: sort category components, asset keys, source paths, moves, skips, and warnings before serialization or display.
- Reuse the existing root/candidate identity validation and per-vault lock conventions (`bin/cleanup_versions.py:98-176`) rather than create a second definition.
- Do not alter cleanup grouping, semantic ranking, exact-tie protection, or unparseable-family protection (`bin/cleanup_versions.py:35-70`).
- Never remove the vault root, category destination directories, non-empty directories, or either excluded tree.

## Architecture

### Inputs and validation

1. Load server/CLI-owned `state/assets.json`; its versions provide `file`, `size_bytes`, and `folder_hint` (`bin/index_assets.py:471-509`).
2. Strictly rescan the configured root using the metadata-only scanner (`bin/index_assets.py:118-148`). Compare the live `(rel_path, size)` manifest with `index_assets.manifest_of(data)` (`bin/index_assets.py:1636-1647`). A mismatch aborts with “run Resync”; no move is planned from stale state.
3. Validate root identity, each source as a regular non-symlink archive, and all existing source/destination ancestors. Reuse `cleanup_versions.SafetyError`, root containment, suffix checks, and `_cleanup_lock` from `bin/cleanup_versions.py:98-176`. Source identity binds `(st_dev, st_ino, st_size)` — deliberately not `mtime_ns`, which this repo documents as OneDrive-churned noise (`bin/index_assets.py:1628-1632`); an mtime-only change is non-drift. _(RT#2)_
4. Treat `_Quarantine` / `_Unresolved` as forbidden destination components compared after NFC normalization and casefolding, and validate the resolved destination path for root containment and real-directory ancestry — matching the scanner's resolved-path exclusion contract, not name equality (`bin/index_assets.py:121-134`). _(RT#14)_

### Plan transformation

For each asset, in `asset_key` order:

- `non_store` -> one asset-level `non-store` skip plus affected file count.
- Missing, folder-derived, or empty category -> one asset-level `no-category` skip with `asset_key`, display name, and file count, split for the caller into pending-queue members (Enrich can resolve) versus resolved-without-breadcrumb assets (needs an `overrides.json` entry — the pending-only Enrich job can never revisit them). _(RT#12)_
- Otherwise validate every category component after whitespace trimming and NFC normalization. Reject empty/dot components, separators, NUL/control characters, absolute values, and excluded-directory names compared with casefolding (a store level of `_quarantine` must not resolve onto `_Quarantine` on APFS). Do not replace unsafe characters: replacement could alias two store categories. At plan time, `lstat` each destination ancestor chain: an existing non-directory or symlinked ancestor yields a `blocked-ancestor` skip carrying the offending path, so a stray file can never abort a confirmed batch opaquely. _(RT#9, RT#14)_
- For each version, set `src` to its indexed relative path and `dst` to `"/".join(levels + [basename(src)])`.
- Same live file at `src` and `dst` -> `already-correct`.
- Occupied/broken-symlink destination, or multiple candidates with the same NFC-normalized, case-folded destination -> `collision`; retain all occupants and candidates. _(RT#14)_
- Otherwise emit a move carrying `asset_key`, category, `src`, `dst`, `size_bytes`, and `folder_version_warning`.

The returned plan contains only repo-relative paths. Absolute root paths and filesystem identities stay inside the engine/server safety snapshot.

### Apply flow

`apply_organize` receives a plan plus the snapshot captured with it:

1. Acquire the same per-vault `flock` used by cleanup (`bin/cleanup_versions.py:167-176`).
2. Before the first move, verify root identity, complete live manifest, every source identity `(device, inode, size)` (mtime-only changes are non-drift), every target's continued absence, real-directory ancestor status (revalidating the plan-time `blocked-ancestor` scan), and target-parent device. Any failure aborts with zero moves. _(RT#2, RT#9)_
3. Create validated category directories under root. Revalidate all targets after creation.
4. For each sorted move: re-`lstat` source and target, then move with `os.link(src, dst)` + `os.unlink(src)`; handle `FileExistsError` as a per-move skip that preserves the destination bytes. Validate the moved inode at the destination. Record the pair before advancing. _(RT#1)_
5. On a move failure, walk the completed ledger in reverse. Move only an identity-matching destination back to an absent original source, using the same no-clobber create. If reversal is incomplete, return every residual path and run one index reconciliation against actual disk before raising. _(RT#1)_
6. After a successful batch, walk only former source parents deepest-first with `os.rmdir`; stop at root, an excluded directory, a destination category, or the first non-empty directory.
7. Call `index_assets.main(["update"])` exactly once, using the cleanup `_update_args` behavior (`bin/cleanup_versions.py:157-163`) so configured output remains correct.

An index-update failure after all moves is reported as `moves_applied: true`; the engine does not falsely claim that confirmed cleanup deletions or already-committed moves were undone. Resync is the reconciliation path.

## Related Code Files

- Create: `bin/organize_versions.py` - planner, snapshot/path validation, apply/rollback, rendering, CLI.
- Create: `tests/test_organize_versions.py` - stdlib `unittest` temporary-vault coverage.
- Reference only: `bin/index_assets.py:118-148`, `bin/index_assets.py:445-519`, `bin/index_assets.py:1636-1647`, `bin/index_assets.py:1783-1904`.
- Reference only: `bin/cleanup_versions.py:22-70`, `bin/cleanup_versions.py:94-176`, `bin/cleanup_versions.py:211-315`.
- Reference only: `tests/test_cleanup_versions.py:15-129` - fixture and safety-test style.

## Implementation Steps

### Tests Before

1. Create temp-vault builders matching `tests/test_cleanup_versions.py:15-20` and zero-byte/small archive fixtures; keep `unittest.main()` as the final statement.
2. Write planner tests first:
   - full `3D/Props/Weapons` destination with source publisher/asset folders flattened;
   - basename and extension unchanged;
   - non-store and folder/empty-category assets skipped;
   - already-correct source skipped;
   - existing destination and duplicate planned destination classified as collisions;
   - invalid/traversing/excluded category or source rejected;
   - `folder_hint` produces a warning but not a renamed destination;
   - output order stable under reversed asset/version input.
   - case/NFD-variant excluded-name and collision aliasing (a `_quarantine` category level; NFC vs NFD basenames in one category);
   - a non-directory or symlinked destination ancestor yields a `blocked-ancestor` skip, not a hard abort.
3. Write apply safety tests first:
   - dry-run CLI makes no directories, moves, or index calls;
   - same-size source replacement and manifest drift abort before the first rename;
   - a destination appearing after plan creation aborts and its bytes remain unchanged;
   - a target-parent symlink or different-device target is rejected;
   - injected second-move failure reverses the first move;
   - success moves every candidate, removes only empty former parents, leaves excluded/non-empty trees, and calls update exactly once with correct override arguments.
   - a destination appearing between plan and apply is skipped via the `FileExistsError` path with its bytes intact (mechanism test, not only plan-time collision);
   - an mtime-only change on a source does not abort apply;
4. Add a no-hydration guard: patch archive read entry points to raise while plan/apply succeeds using metadata and rename operations.

### Refactor / Implementation

5. Implement narrow helpers for category validation, contained relative-path resolution, case-folded collision keys, plan rendering, and empty-parent removal inside `organize_versions.py`; do not create a generic framework.
6. Implement `plan_organize` as a pure/deterministic transformation after the explicit live-manifest validation boundary.
7. Implement snapshot capture and `apply_organize` around the existing cleanup safety primitives. Keep plan-time skip decisions separate from apply-time hard preflight failures.
8. Implement the dry-run/`--apply` CLI. Load root from `index_assets.load_config()` and state from `index_assets.state_dir()` (`bin/index_assets.py:1783-1811`); never add a second config source.

### Tests After

9. Run `python3 -m unittest tests.test_organize_versions`.
10. Inspect one dry-run plan against a temporary mixed tree and assert zero file-content reads, exact source/destination paths, folder-version warnings, collision preservation, and no filesystem mutation.

## Success Criteria

- [ ] Full-depth store breadcrumb used; ad-hoc source parents discarded; filename and extension unchanged.
- [ ] Non-store and no-store-category assets never move and are countable separately.
- [ ] `_Quarantine` and `_Unresolved` cannot be source, destination, or cleanup targets.
- [ ] Existing and planned destination collisions are deterministic skips; destination bytes never change.
- [ ] Every folder-supplied-version move is flagged before apply; no rename workaround is introduced.
- [ ] Any stale index, root/source identity drift (path, size, inode — not mtime), symlink, target appearance, or cross-device target aborts before mutation. _(RT#2)_
- [ ] Mid-batch failure reverses completed safe moves or reports exact residual paths and reconciles the index.
- [ ] Successful apply removes only empty former parents and invokes `update` once.
- [ ] Targeted stdlib test module passes without opening an archive.

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation / trigger / response |
|---|---|---|---|
| Store category data contains a separator or traversal component | Low | High | Reject the complete plan; trigger is component validator failure; correct enrichment/override, then re-plan |
| Case-insensitive/NFD destination alias not visible with exact-string comparison | Medium | High | NFC-normalized, case-folded destination keys and resolved-path containment checks; collisions skip every contender _(RT#14)_ |
| Destination appears between preview and apply | Medium | High | Hash includes snapshot; preflight plus immediate target `lstat`; return drift and require a new plan |
| `os.link` hits an occupied destination (race or alias) | Low | High | Atomic-failure create instead of `os.rename`/`os.replace`; per-move `FileExistsError` skip preserves destination bytes; NFC/case-folded plan keys; all-target preflight, shared lock _(RT#1, RT#14)_ |
| Move fails after earlier moves | Low | High | Reverse identity-checked ledger; if rollback incomplete, report residuals and re-index actual disk |
| Empty-dir cleanup removes a meaningful folder | Low | Medium | `os.rmdir` only on recorded former parents; stop at non-empty/root/excluded/destination boundaries |
| Folder-derived version becomes unparseable | Certain for warned rows | Medium | Explicit preview warning; preserve filename per decision; existing cleanup protects unparseable families |
| OneDrive hydrates archives | Low | High | No archive open/read/hash/copy calls; metadata and rename-only test gate |

## Rollback Plan

Before apply, discard the plan. During apply, reverse the completed move ledger before returning failure. After a successful apply, use the confirmed plan's `src` / `dst` pairs in reverse and run `index_assets.py update`; this is an operator action, not a new undo feature. Removing this phase's code requires deleting only the two new files because existing cleanup/index code remains unchanged.
