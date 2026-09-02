---
title: "Conservative Unity Asset Store vault version cleanup"
description: "Fail-closed removal of strictly superseded Unity Asset Store archives, followed by an index update."
status: completed
priority: P1
effort: "1d"
tags: [unity, asset-management, tooling, safety]
created: 2026-09-02
mode: tdd
---
# Conservative Unity Asset Store vault version cleanup

## Outcome
Rescan the configured Unity Asset Store vault and remove only archives that are strictly superseded by a newer version within the same parsed asset identity. Retain semantic-version maximums; use filename release date only as a tie-breaker. Refresh the existing index through `index_assets.py update`.

## Constraints and non-goals
- Reuse `bin/index_assets.py` scanner/parser/key/version helpers; no dependencies.
- Never open archives or hydrate OneDrive placeholders.
- Group by `asset_key` so pipeline and discriminator variants remain independent.
- Preserve any family with an unparseable version or multiple exact maximum `(version, date)` ties.
- Never use mtime, file size, fuzzy names, or stale state as deletion authority.
- Existing scanner CLI and generated state schema remain unchanged.

## Implementation
- [x] Add `bin/cleanup_versions.py` as a separate destructive workflow.
- [x] Add `tests/test_cleanup_versions.py` using stdlib `unittest`.
- [x] Default mode is deterministic dry-run: list survivor/removals/reasons and totals; no unlink or state writes.
- [x] `--apply` requires full-root and per-candidate `lstat`/manifest validation, rejects symlinks, non-regular files, unsupported suffixes, absolute/parent-traversing paths, and aborts on any drift before the first unlink.
- [x] Unlink only validated candidates, then invoke existing `index_assets.py update` exactly once with forwarded root/state options. Normal configured runs omit `--root` so `config.json` output routing is preserved.

## Accepted live baseline
Accepted pre-apply scout: 880 archives, 844 asset identities, 34 multi-version identities. Conservative cleanup predicted 26 removable paths across 24 identities, 7,877,694,079 reclaimable bytes, and 854 remaining archives. Eight exact-tie families and two parsed/unparsed families remained untouched.

Applied result: 26 superseded archives removed from the configured vault; exact ties and unparseable families preserved. The regenerated index contains 854 files and 844 assets, with 6 pending enrichments.

## Verification
- [x] Unit tests cover version/date ranking, stable-vs-prerelease behavior, ties, unparseable families, pipeline/discriminator isolation, dry-run non-mutation, path safety, drift rejection, and update handoff.
- [x] Full stdlib test suite: 234/234 passing.
- [x] Live dry-run reviewed against the accepted baseline; version-then-date policy applied, with exact ties and unparseable families preserved.
- [x] Apply completed: 26 deletions; post-apply vault has 854 archives; all 26 removed candidates are absent; quarantine files: 0.
- [x] Index update wrote 854 files / 844 assets and 6 pending enrichments; no archive content opened and no unrelated files changed.

## Completion sync
**Status:** Completed  
**Progress:** 100% (10/10 tracked items; 0 phase files; 0 unresolved mappings).

**Verification evidence:** final code review PASS; `py_compile` PASS; targeted cleanup 10/10; full `unittest` 234/234; live post-apply checks passed.

**Docs/API impact:** None identified for this sync. Existing scanner CLI and generated state schema remain unchanged; cleanup is an internal maintenance workflow. No docs-manager dispatch.
