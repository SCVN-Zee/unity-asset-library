---
title: "OneDrive native organize moves"
description: "Replace hard-link moves with atomic exclusive renames and verify cloud hydration behavior."
status: completed
priority: P1
effort: ""
tags: [bugfix, backend, safety]
created: 2026-09-06
---

# OneDrive native organize moves

## Contract
- Outcome: Organize category folders without downloading online-only archive contents.
- Constraints: preserve filenames, no-overwrite semantics, identity checks, reverse-order rollback, state reconciliation, metadata-only indexing and existing HTTP/CLI schemas. Python stdlib only.
- Non-goals: mass-organizing the real vault during verification; changing cleanup, classification, UI, sync settings or implementing speculative Microsoft authentication.
- Acceptance: native moves preserve online-only state in a disposable OneDrive probe; collisions preserve both files; batch failure reverses earlier moves; rollback collisions preserve both files and report residuals; re-index reflects successful moves; full existing suite passes.

## Evidence and direction
Python backend (`bin/organize_versions.py`) serves both CLI and Electron/React HTTP actions (`bin/server.py:372-386`). Before this fix, apply and rollback used os.link plus os.unlink. Scanner only reads filesystem metadata (index_assets:119-149). The former no-open test did not prove absence of OS-triggered hydration.

Use Darwin `renamex_np(RENAME_EXCL | RENAME_NOFOLLOW_ANY)` through ctypes, with no content-copy or hard-link fallback. SDK `sys/stdio.h` defines flags 4 and 16. Other importable POSIX hosts fail closed on apply; Windows portability is not introduced. Register attempted moves before the syscall so a caught exception after commit remains recoverable. Rollback distinguishes an unchanged source from a committed destination by identity; never removes an unrelated occupant.

Cloud behavior is a verification gate, not a promise derived from API choice. If native rename hydrates the disposable file, stop claiming completion and revisit Graph cloud-side moves (requires authenticated drive access). Do not silently fall back to downloads.

## Phase
1. [Native moves and verification](phase-01-start.md): engine, regression cases, disposable cloud probe, CLI smoke and full regression suite.

## Flow
Confirmed plan → preflight → exclusive native rename → identity validation → repeat → remove empty former parents → existing index update.
Caught move-stage failure → reverse attempted ledger using exclusive rename → report residuals → existing reconciliation. Index-update failure preserves successful moves and asks for Resync; process-crash recovery is unchanged.

## Validation
No public schema or server call changes. Existing platform target is macOS (`package.json:47`). Native API flag grounded in local SDK. Test command: `python3 -m unittest discover -s tests`; renderer build: `npm run build`. No broad vault mutation is authorized by the test plan.

## Review
Two independent reviewers checked security and assumptions. Accepted: add RENAME_NOFOLLOW_ANY; measure cloud identity and residency; rollback covers caught in-process move-stage failures only. Index-update failure retains applied-but-needs-Resync behavior. Crash recovery and Windows portability are non-goals (backend already requires fcntl). The platform guard covers otherwise importable POSIX hosts; no new concurrent-directory-creation safety guarantee is claimed. Cloud proof remains an execution gate.

## Cloud experiment
Two disposable 65,536-byte files were synced to the configured OneDrive folder and evicted using Finder Free Up Space. Both started with SF_DATALESS and zero allocated blocks. Native rename with flags 0x14 retained flags=1073741920, blocks=0, and the same dev/inode/size through immediate, 2-second, 10-second and reverse-move samples. The hard-link control changed to flags=64 and blocks=128: it materialized the entire file. A later independent process still observed the rename file as dataless; device numbers differed between process observations, so identity evidence is explicitly scoped to the same-process operation window.
Full application CLI dry-run and apply passed against isolated local and real OneDrive fixtures with real index refresh. The cloud file remained SF_DATALESS, zero blocks, same within-process identity immediately and at 2/10 seconds. Final engine rollback also passed on that online-only file.

## Verification results
- Python full suite: 259 tests passed, including 31 organizer tests.
- Desktop bootstrap: node --test tests/test_desktop_bootstrap.cjs passed.
- npm run build: passed typecheck and Vite build; existing 500 kB chunk advisory remains.
- Existing index tests emit unclosed-file ResourceWarnings; no test failures.
- Independent implementation review found a missing source-identity check when rollback destination was absent. Fixed and covered with a same-size replacement fault regression; collision tests verify preserved bytes.
- No API/schema/UI changes; no real asset collection was organized.

## Operational note
Restart the running desktop/backend to load the changed Python module. Organize uses atomic exclusive native moves on macOS, without copy/link fallback. A caught mid-move failure is reversed when identities still match; an occupied rollback source is never overwritten. The unchanged index-refresh failure path asks for Resync. A provider/system change can still invalidate a snapshot and fail closed; the measured result is not a universal guarantee about all future OneDrive versions.
