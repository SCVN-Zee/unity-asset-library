---
phase: 1
title: "Truthful engine progress"
status: completed
priority: P1
effort: ""
dependencies: []
---

# Truthful engine progress

## Goal
Expose actual metadata/file work to an optional observer without changing the operations.

## Owned files
Create bin/progress.py; modify bin/index_assets.py, bin/cleanup_versions.py, bin/organize_versions.py and their existing tests only.

## Steps
1. Implement emit_progress(callback, stage, completed=0, total=None, current_item=None, **counts) and json_progress(event) with the shared plan schema and UAI_PROGRESS prefix. Keep existing CLI quiet unless explicitly requested; no added dependencies.
2. Add final optional progress=None keyword to scan/main and apply_organize/apply_cleanup and relevant private helpers. Forward through nested preflight/reconcile/update so expensive hidden scans are visible. Normal callers work unchanged. No observer exception may cause incomplete file bookkeeping; publish after ledger updates and keep observer delivery non-fatal.
3. Scan: total unknown while walking, count found archives and current relative path before stat; final scan count truthful. Update: distinguish build/compare/merge/write/emit stages; known record loops can expose done/total. No second pre-count scan.
4. Organize: validate, prepare, move (processed includes skipped, factual moved/skipped counters), rollback, reconcile, refresh. Publish current item before native move and completed only after outcome known. Never change native rename flags, hash material, identity checks, lock scope or error contract.
5. Cleanup: distinguish reversible staging from committed removal; progress never calls staged files deleted. Rollback count is separate from forward count. Only publish removed after successful unlink. Preserve current failure/reconciliation behavior.
6. Add --progress-json to index CLI; prefer a callback passed by caller, otherwise explicit flag selects json_progress. Optional callback must not re-introduce recursive state locks.

## Verification
Run focused engine suites, including a plausible observer failure case proving files/ledger remain safe. New tests only where observable progress boundaries prevent false completion/counting. Main will run real temp-vault flows and full suite. Coordinate exact callback contract with job worker before changing it.

Completed: optional structured observers cover metadata scan, planning, mutation, rollback and refresh. Actual native moves/cleanup and state-lock handoff passed disposable HTTP smoke; observer-failure and rollback tests pass. Final full Python suite: 269 tests OK.
