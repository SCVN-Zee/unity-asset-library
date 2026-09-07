---
phase: 1
title: "Native moves and verification"
status: completed
priority: P1
effort: ""
dependencies: []
---

# Native moves and verification

## Goal
Remove hard-link hydration triggers without sacrificing no-clobber safety.

## Files
- Modify `bin/organize_versions.py`: private ctypes exclusive-rename helper; apply and rollback ledger migration; accurate comments.
- Modify `tests/test_organize_versions.py`: migrate fault injection to move seam; replace obsolete unlink-stage case with committed-move exception recovery; collision, rollback collision, unsupported-platform and content preservation coverage.
- Update this plan with results and a journal after verification. Historical completed plans stay historical.

## Steps
1. Review platform support and source/destination safety. Resolve review blockers before edits.
2. Bind Darwin renamex_np with explicit ctypes signature/use_errno and RENAME_EXCL | RENAME_NOFOLLOW_ANY; propagate errno as OSError (FileExistsError on EEXIST). Fail closed before apply mutations if unavailable, without copy/link fallback. The existing POSIX import boundary is unchanged.
3. Record attempted `(src, dst, identity)` before moving; discard collision entries; count only successful moves. On rollback, unchanged original with no committed destination needs no mutation; identity-matching destination with absent source is reversed exclusively. Mismatched destinations/source occupants remain untouched and residuals are reported. Catch path validation errors per rollback entry so reconciliation still runs.
4. Migrate tests to observable failure states rather than link/unlink plumbing. Keep no-content-read regression, label it honestly.
5. Create only disposable probe files in OneDrive, allow sync, use Free Up Space, require SF_DATALESS before measuring. Compare native rename and hard-link control on separate disposable files. Observe flags/blocks before and after and after a bounded settle; avoid reading archive contents until residency measurements finish. Clean only owned probe artifacts.
6. Run real CLI organize against an isolated fixture with real index refresh. Run focused and full unittest suites and renderer build.

## Verification gates
- Existing destination (including dangling symlink) never overwritten.
- Failure before and after syscall commit recover correctly.
- Rollback collision leaves original occupant and moved file untouched with residual report.
- No unsupported-platform fallback reads/downloads data.
- Cloud probe either proves online-only residency survives or explicitly blocks the outcome. Local temp tests alone cannot satisfy this gate.

## Review clarifications
Rollback applies to caught in-process move-stage failures, not process death or a failed index refresh after successful moves. Preserve the existing applied-but-needs-Resync behavior. Cloud measurement also checks device/inode/size within the same process. No new Windows support or durable crash journal is introduced.
- No real collection organization during smoke; HTTP contract remains unchanged and is covered by server regression suite.

## Completed verification
Native API A/B probe reproduced hard-link hydration and showed native move/rollback preserving online-only status. Actual CLI dry-run/apply with real re-index passed locally and on the disposable cloud file. Final rollback ran on the cloud file with zero blocks and unchanged identity. Full Python suite: 259 pass; organizer: 31 pass; desktop bootstrap and renderer build pass. Independent review source-replacement rollback defect fixed with a regression. Scope remained limited to engine/tests; server and UI contracts unchanged.
