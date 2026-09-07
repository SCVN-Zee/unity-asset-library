---
phase: 2
title: "Pollable action jobs"
status: completed
priority: P1
effort: ""
dependencies: []
---

# Pollable action jobs

## Goal
Serve responsive progress and terminal results while existing business operations run under unchanged safety gates.

## Owned files
Modify bin/server.py, bin/resolve_store.py, tests/test_server.py, tests/test_resolve_store.py only. Engine callback contract is in plan.md/phase 1; UI bridge is phase 3.

## Steps
1. Add latest _action separate from _job with atomic admission: one pending/running action allowed. IDs server generated; accept only existing six allowlisted action kind/phase paths, unchanged exact body validation. HTTP returns 202 job_id, API version 4. Keep synchronous op methods as worker business logic and add optional progress callbacks. Do not hold _job_lock while invoking engines.
2. Snapshot ActionJob per plan; GET /api/action/id plus state.action. Worker stores result once complete. Catch Busy, Drift, StaleIndex, SafetyError, OSError and unexpected worker errors into distinct terminal status/error_code/result. Drift retains replacement preview/hash. Do not publish success until business operation/index update returns. Maintain enriched remaining count after real completion.
3. Forward optional progress through scan/apply/update seams and publish boundaries for plan comparison/snapshot validation. Update tests' seams explicitly rather than introspection or TypeError fallback. Polling must work while locks held and current_item must be server-derived.
4. Resolver emits structured stdout progress with --progress-json using progress.py. Publish current asset before work, counters after known outcome; blocked/error paths count attempts honestly, preserve remaining/failed information, do not fake whole-job completion from resolve percentage. Emit merge stage progress where possible, with real totals only. Default human logs unchanged.
5. Enrich worker passes progress flag to resolver/index subprocesses, parses only structured event prefix for progress, validates basic schema, updates snapshots under lock, leaves human lines as bounded log only. Remove old regex count parser. Reset stage totals and expose waiting-for-lock stages without holding job lock during acquisition. Retain cancellation semantics and avoid cancelled status being overwritten by late events.
6. Handle terminal action replacement/unknown IDs and safe shutdown using existing lifecycle conventions; no history or queue. Explicit timing in epoch seconds for UI elapsed calculation.

## Verification
Migrate existing HTTP tests to 202+poll terminal result while keeping security/admission assertions. Add deterministic event-blocked worker tests proving intermediate state + GET responsiveness, action/enrich isolation, wrong hash gives failed plan_changed without mutations, thrown worker failure reaches terminal. Test structured resolver event/no-human-regex behavior and counters on zero/failed/skipped cases. No live network or real-vault changes.

Completed: API v4 async routes, independent Enrich snapshots, structured resolver counters and cancellation-aware lock waits. All six real fixture routes, stale-hash refusal, zero-pending subprocess pipeline, and cancellation while external locks remain held passed. Index-only removals use index_removed, never deleted-file counts.
