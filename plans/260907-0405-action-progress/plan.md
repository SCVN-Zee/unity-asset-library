---
title: "Live action progress"
description: "Show truthful stages, item counts, current work and retained outcomes for every library action."
status: completed
priority: P1
effort: ""
tags: [feature, frontend, backend]
created: 2026-09-07
---

# Live action progress

## Accepted contract
- Outcome: Resync, Organize, Cleanup and Enrich visibly report current stage, real completed/total counts when known, current file/asset, elapsed time and retained terminal summary. Feedback stays visible after Actions closes and while an apply dialog is open.
- Constraints: preserve confirmations, fresh plan/hash validation, existing lock scopes, native no-download moves, metadata-only scans, rollback and applied-but-index-refresh-failed semantics. No fabricated global percentages; unknown scan totals stay indeterminate. Preserve existing design tokens/components.
- Non-goals: job history/queues, new cancel semantics, estimated completion time, archive content reads, sync settings, Graph integration, bulk real-vault execution.
- Acceptance: all six preview/apply flows and Enrich produce observable progress; polling stays responsive during blocked work; terminal success is only after refresh; drift requires another confirmation; failures/rollback/cancellation remain visible; running Enrich and an action never overwrite each other's feedback; reload can recover latest running status.

## Evidence
Electron/React UI currently swaps button text for preview/apply and polls only Enrich every 900 ms (App.tsx:224-247,319-396). Enrich reports stage but not counts (779-797). Backend plan/apply handlers block until completion; job polling does not take mutation locks (server.py:284-294,427-446). Scan is one metadata-only walk (index_assets.py:119-149); its total is initially unknown. Organize and cleanup have known candidate totals after planning. Existing HTTP API version is 3.

## Direction and interfaces
Use asynchronous plan/apply requests on the existing six POST URLs, cleanly changing their successful response to HTTP 202 `{job_id}`. Bump API version to 4 and migrate Electron/bootstrap checks and all renderer/test callers. Keep synchronous op methods as private worker business operations, not alternate public endpoints. Enrich retains its existing start/cancel URLs and independent job. No event-stream dependency.

`GET /api/action/<id>` returns `{action: ActionJob}`; unknown ID returns 404 `unknown_action`. `/api/state` gains `action` (latest action or null) while retaining `job` for Enrich. One action may run at a time; another action start gets Busy without replacing it. Latest terminal record remains until next action. UI must use captured IDs and never assume missing jobs succeeded. Enrich may coexist under the existing mutation gate behavior.

ActionJob fields:
- `id`, `kind` (resync/cleanup/organize), `phase` (plan/apply), `status` (queued/running/completed/failed).
- `stage` string; `completed` integer; `total` integer or null; `current_item` string or null; `counts` object for factual summary counters.
- `started_at` UNIX seconds; `finished_at` UNIX seconds or null.
- `result` ActionResult or null; `error` text or null; `error_code` string or null. On drift, result is `{preview, plan_hash}` and error_code is `plan_changed`; UI retains fresh preview and requires explicit reconfirmation. Busy/stale/failure stay failures, not success.

Enrich snapshots add the same progress/timing fields, with kind=enrich and phase=run, retaining existing status/counts/remaining/error/log_tail. Cancellation remains Enrich-only. Unknown total resets each stage; completed resolve is not whole-job completion. Backend publication/snapshot uses the same short job lock.

Engine observer contract: final optional `progress=None` callback on scan/main/apply paths (and private helpers as needed). Callback receives `{stage,completed,total,current_item,counts}`. New small `bin/progress.py` owns `emit_progress(callback, stage, completed=0, total=None, current_item=None, **counts)` and `json_progress(event)` (stdout prefix `UAI_PROGRESS ` plus JSON, flushed). No I/O if callback absent. Server callbacks merge factual counters and atomically replace stage fields. Never put progress in plan hashes. Throttle item events in producer/server as needed without losing final updates.

Resolver and index CLI accept `--progress-json`; Enrich worker passes it. Emit structured events from actual work, not regex-parsed human logs; remove old total/step regex progress parsing. Counts must include all examined outcomes accurately and distinguish resolved/failed/skipped. Current item set before potentially slow work. Fatal aborts never imply all work finished.

## Delivery slices / ownership
1. [Engines](phase-01-start.md): progress.py, index_assets.py, cleanup_versions.py, organize_versions.py, associated engine tests.
2. [Job API](phase-02-jobs.md): server.py, resolve_store.py, test_server.py, test_resolve_store.py.
3. [UI and bridge](phase-03-ui.md): app/src, electron/main.cjs, electron/preload.cjs, test_desktop_bootstrap.cjs.
Independent writes start after review against the shared interface above. All slices converge before integration verification. No worker owns another slice's files.

## Verification
- Existing Python full suite plus deterministic blocked-worker polling, collision/drift safety and structured resolver-event regression cases.
- Real local temporary-vault HTTP run: resync preview/apply, organize preview/apply, cleanup preview/apply; check actual files/index, no real-vault mutations.
- Renderer: Vite + injected sandbox bridge, then real loopback backend bridge on disposable fixtures where feasible. Visually inspect determinate/unknown progress, dialog apply, terminal failure, drift, cancellation, concurrent Enrich/action, narrow viewport and keyboard access.
- `npm run build`; `node --test tests/test_desktop_bootstrap.cjs`.

## Risks and boundaries
No new durable recovery guarantee on process crash. Restart invalidates in-memory jobs; UI reports lost connection/unknown operation instead of success. The shared progress observer must not weaken file safety or hold job lock around operations. Cleanup reports staging separately from irreversible removal; rollback is its own stage. Render progress once per operation in the visible main panel, with the same component inside blocking apply dialog rather than hidden behind it.

## Review
Two independent reviewers checked lifecycle/failure and safety/scope. Resolutions below are binding; implementation may proceed.

## Review resolutions
- Preserve exact HTTP/CSRF allowlists and perform replan/hash validation in executing worker, not admission. API v4 intentionally changes execution-time Busy to failed action error_code=busy after 202; active-action admission returns 409. No queued retry or waiting on action mutation locks. Waiting stages apply only to Enrich, which already waits.
- Structured Drift result preserves preview/hash; UI reconfirmation mandatory. Renderer reload recovers while same backend lives; backend restart is unknown outcome, never automatic retry/apply.
- SafetyError gains optional result metadata (one-message calls unchanged). Engines report applied/state_refreshed/counts/rollback_incomplete/residuals on post-mutation failure as appropriate; never parse human error strings. Nonzero cleanup/organize index update must not report success.
- completed/total/current_item reset per stage. Named counters moved/skipped/staged/removed/rolled_back/rollback_failed are operation-cumulative, not summed into a universal percentage; retain on error. Observer exceptions must not affect file operations.
- Track action thread; reject starts after shutdown begins, drain active action before releasing instance lock outside job lock. No new cancel or crash journal.
- Native/metadata-only regression coverage exercises observers enabled. Throttling preserves stage/final values. Structured subprocess events validated; all three Enrich stages receive flag.
- Validation: source paths, version guard, HTTP handlers, native move/cleanup return paths inspected. Whole-plan interface and ownership consistent. No unresolved user decisions; user requested implementation.

## Completion evidence — 2026-09-07
- Delivered all four actions with live stages, genuine per-stage counts, current item, elapsed time and retained success/failure/cancellation summaries. Preview/apply jobs are independent of Enrich. API v4 requires restarting the desktop app/backend together.
- Final verification: Python full suite 269 tests OK; npm run build (TypeScript + Vite) PASS; desktop bootstrap Node test 1 PASS. Existing Python ResourceWarnings and Vite chunk-size advisory remain non-failing.
- Disposable real HTTP fixture: all six plan/apply paths passed. Resync indexed a new version; Organize natively moved 3 files; Cleanup removed 2 older files, retained the latest bytes and refreshed the real index. Stale confirmation failed plan_changed. Real zero-pending Enrich resolve/enrich/emit subprocesses completed without network lookups.
- Renderer proof: injected bridge covered unknown/determinate/zero totals, concurrent panels, apply dialog, stage-only announcements, cancellation, transient polling recovery, backend-restart unknown outcome, drift with a newly confirmed hash, partial rollback/refresh failure, and delayed getAssets success/failure. Real loopback fixture bridge additionally exercised Resync preview/apply and an index-only removal; confirmed no deleted-file counter and dialog closure after actual reload. No real asset collection was mutated.
- Integration fixes: removed nested state-flock deadlock via explicit held-lock forwarding; made Enrich gate/flock waits cancellation-aware; prevented rolled-back attempts from counting as retained changes; separated index/removal and resolver/inventory counters; prevented UI refresh/poll races from reporting premature success. Independent backend/UI review blockers resolved.
- Historical plans intentionally unchanged; current API behavior documented in server module and this plan. Temporary fixtures, smoke scripts and browser/server processes are removed after verification.
