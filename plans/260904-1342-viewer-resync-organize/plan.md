---
title: "Viewer Resync and Organize Actions"
description: "Add local-server-backed Resync, Organize, and supporting Enrich actions to the generated viewer with previewed, fail-closed vault mutations."
status: completed
priority: P1
effort: "22h"
tags: [unity, asset-management, frontend, api, safety, tdd]
created: 2026-09-04
mode: tdd
---

# Viewer Resync + Organize Buttons

## Outcome

Add two primary toolbar actions to the generated viewer:

1. **Resync** rescans only the configured vault, refreshes the index, then previews conservative removal of superseded versions.
2. **Organize** previews archive moves into the full verified Asset Store breadcrumb, flattening ad-hoc publisher and asset folders while preserving every filename.

A supporting **Enrich** action resolves only pending unresolved store assets in a pollable background job, then merges and re-emits the index. Destructive work always follows preview -> explicit confirm -> fresh server-side re-plan -> fail-closed apply -> one index refresh.

## Constraints and non-goals

- Stdlib-only additions. `requests` remains confined to the existing resolver; no new runtime or test dependency.
- Never open archive contents. The scanner is metadata-only and excludes `_Quarantine` / `_Unresolved` (`bin/index_assets.py:118-148`); organize uses `lstat`, directory metadata, and rename operations only.
- Resync scans `config.json`'s `vault_root` only (`config.json:2-3`; `bin/index_assets.py:1793-1811`). It never imports from Unity Hub's download folder.
- No filename changes. A filename whose version currently comes from its parent folder (`bin/index_assets.py:278-284`) becomes versionless after a move; the preview must warn. Existing cleanup still protects any family containing an unparseable version (`bin/cleanup_versions.py:50-63`).
- No auto-organize during scan, no change to cleanup identity/ranking/tie policy, and no automatic deletion without confirmation.
- Non-store assets never move. Assets without a verified store breadcrumb remain in place and appear in the organize report with an Enrich recommendation.
- `_Quarantine` and `_Unresolved` are never sources, destinations, or empty-directory cleanup targets.
- No auth, remote access, arbitrary file serving, or client-supplied filesystem paths. The server binds only `127.0.0.1` and accepts only an optional port.
- No collision overwrite. Existing or multiply-planned destinations are skipped; apply rechecks every destination.

## Decided choices

| Decision | Locked behavior |
|---|---|
| Resync source | Configured vault rescan only; no Unity Hub folder |
| Organize depth | Every verified breadcrumb component, e.g. `3D/Props/Weapons/<filename>` |
| Folder flattening | Destination is breadcrumb + source basename; publisher/asset folders are not carried forward |
| Filename policy | Never rename; accepted folder-supplied-version loss is warned before confirmation; apply is no-clobber by construction — atomically-failing `os.link` + `os.unlink`, never `os.rename` onto an occupied path _(RT#1)_ |
| Confirmation drift | Opaque SHA-256 approval hash over the normalized plan, the path manifest, and `(dev, ino, size)` archive identities — deliberately excluding `mtime_ns`, which OneDrive churns by documented design (`bin/index_assets.py:1628-1632`); mismatch returns HTTP 409 with a replacement plan and requires a new confirmation _(RT#2)_ |
| Concurrency | Three state-dir `flock` files with separated concerns: lifetime `server-instance.lock` (single instance, any port), operation-held `state-write.lock` (server writers non-blocking -> busy 409; cooperative CLI `update`/`enrich`/`emit` blocking), and `cache-write.lock` (fail-fast on every cache-mutating path — shared `resolve`/`spike` flow and `import-ids` — guarding whole-snapshot `cache.json` rewrites against concurrent writers); the Enrich `resolve` stage takes only `cache-write.lock`, so Resync stays available; destructive engines keep the existing vault `flock` _(RT#3, RT#5, RT#7)_ |
| Enrich scope | Background subprocess runs resolution with `--resume --pending`, then existing `enrich`, then `emit`; no full retry sweep; the network `resolve` stage is gate-free (writes only `cache.json`) while `enrich`/`emit` take the mutation gate, so Resync always stays available during resolution and only briefly busy-blocks on the merge/emit stages; `POST /api/enrich/cancel` terminates it safely _(RT#5)_ |

## Goals

| # | Goal | Priority | Observable result |
|---|---|---|---|
| 1 | Resync from the viewer | P1 | Fresh index plus cleanup preview containing files, identities, protected families, and reclaimable bytes |
| 2 | Apply cleanup safely | P1 | Confirmed superseded files removed; exact ties and unparseable families preserved; regenerated viewer reflects disk |
| 3 | Organize by full store breadcrumb | P1 | Eligible archives move to full-depth categories without renaming or overwriting; obsolete empty source directories disappear |
| 4 | Enrich unresolved assets | P1 | Pending-only background job exposes bounded progress/log state and refreshes the viewer when done |
| 5 | Preserve static viewer use | P1 | All three actions remain visible but disabled on `file://`, with a `python3 bin/server.py` hint |

## Data flows

### Resync and cleanup

`Resync click` -> `POST /api/resync` -> strict vault pre-scan (walk error = clean 503, zero writes) -> existing `index_assets.main(["update"])` scan/build/cache-merge/emit flow (`bin/index_assets.py:1815-1903`) -> strict fresh scan -> existing `plan_cleanup` (`bin/cleanup_versions.py:39-70`) -> preview + snapshot hash -> modal. Confirm sends only the hash -> server rescans, replans, and rehashes (identity = path + `(dev, ino, size)`; `mtime_ns` ignored as documented OneDrive noise) -> mismatch returns 409/new preview; match enters existing snapshot/preflight/lock/apply path (`bin/cleanup_versions.py:139-176`, `bin/cleanup_versions.py:211-294`) -> reload generated viewer. The response always carries an explicit `state_refreshed` status. _(RT#2, RT#10)_

### Organize

`Organize click` -> `POST /api/organize/plan` -> read repo-owned `state/assets.json` -> pair each indexed version with the live metadata scan -> require store-sourced `category.levels` (store merge writes these at `bin/resolve_store.py:751-756`) -> validate components after NFC normalization, reject case/normalization-variant excluded names, and verify resolved-path containment and real-directory ancestry -> derive `breadcrumb/basename` -> classify moves/skips (including `blocked-ancestor`)/warnings -> preview + snapshot hash. Confirm replays the plan server-side; a match performs preflighted, no-clobber `os.link`+`os.unlink` moves, reverses completed moves on mid-batch failure, removes only empty former parents, calls `update` once, then reloads. _(RT#1, RT#9, RT#14)_

### Enrich

`Enrich click` -> `POST /api/enrich/start` -> one background job -> fixed-argument subprocess `resolve_store.py resolve --resume --pending` (gate-free; writes only `cache.json`) -> per-asset atomic cache saves (`bin/resolve_store.py:960-1025`) -> `resolve_store.py enrich` merges cache and prunes only resolved queue entries under the mutation gate (`bin/resolve_store.py:914-933`) -> `index_assets.py emit` under the same gate -> `GET /api/job/<id>` polling (404 `unknown_job` after a server restart is a terminal client state) -> reload. The `resolve` stage never blocks Resync; `enrich`/`emit` briefly busy-block destructive endpoints. The new resolution scope reuses `filter_worklist(..., pending_keys)` (`bin/resolve_store.py:483-493`); unresolved and no-result records remain retryable (`bin/resolve_store.py:935-944`). `POST /api/enrich/cancel` terminates the tracked child; per-asset atomic cache writes make cancel and restart safe. _(RT#5, RT#13)_

## Dependencies and file ownership

No unfinished cross-plan blocker exists. Phases run in order; no parallel phase writes the same file.

| Phase | Dependency | Owned files | Effort |
|---|---|---|---|
| 1. [Organize Engine](./phase-01-organize-engine.md) | None | Create `bin/organize_versions.py`, `tests/test_organize_versions.py`; one-line pre-step edit to `tests/test_index_assets.py` | 7h |
| 2. [Local Server](./phase-02-local-server.md) | Phase 1 | Create `bin/server.py`, `tests/test_server.py`; modify `bin/resolve_store.py`, `tests/test_resolve_store.py`, `bin/index_assets.py` (shared output_dir helper) | 9h |
| 3. [Viewer Wiring](./phase-03-viewer-wiring.md) | Phases 1-2 | Modify `bin/index_assets.py`, `tests/test_index_assets.py`; regenerate `index.html` | 6h |

## Implementation

- **Phase 1:** Build a deterministic dry-run organize planner and rollback-aware apply engine. Reuse the current parser, state schema, root checks, archive identity checks, and vault lock rather than introducing a second safety convention.
- **Phase 2:** Add the loopback-only static/API server, stateless hash confirmations, one server-lifetime mutation gate, pending-only resolution scope, and a bounded in-memory job record. Keep every filesystem target server-derived.
- **Phase 3:** Add disabled-by-default toolbar controls, server readiness state, accessible plan modal, 409 refresh handling, and Enrich polling to `HTML_TEMPLATE` (`bin/index_assets.py:686-1598`). Regenerate `index.html` only through the existing emit pipeline (`bin/index_assets.py:1618-1619`, `bin/index_assets.py:1892-1903`).

## Compatibility and migration

- No `assets.json`, cache, pending-queue, or config schema migration.
- Existing `scan`, `emit`, `update`, cleanup CLI, and full resolver behavior remain unchanged except cooperative additions: `state-write.lock` wraps server-side `update`/`enrich`/`emit` execution and the Enrich `enrich`/`emit` stages, with cooperating CLI runs of the same commands acquiring it blocking; `resolve`/`spike`/`import-ids` lifetime-hold `cache-write.lock` with fail-fast contention. `resolve --pending` is additive; absence of the flag retains the current eligible-set behavior. _(RT#3)_
- `file://` remains a fully usable read-only viewer. Actions are disabled until `/api/state` succeeds from the loopback server.
- Existing enriched categories become organize input without rewriting state. Missing/folder-derived categories stay put; pending ones are resolvable by Enrich, while assets resolved without a store breadcrumb need an `overrides.json` entry — the organize report distinguishes the two populations. _(RT#12)_
- Physical organization is the only data migration. Preview lists every move; filenames remain byte-for-byte unchanged. Re-index rewrites paths in generated state after apply.

## Verification

| Layer | Coverage | Gate |
|---|---|---|
| Unit | Organize destination derivation, skip classes (incl. `blocked-ancestor`), unsafe components, NFC/case-fold aliasing, full-depth flattening, planned/existing collisions, no-clobber `FileExistsError` handling, folder-version warning, source/destination drift (mtime-tolerant), rollback, empty-dir cleanup, update-once | `python3 -m unittest tests.test_organize_versions` |
| Unit | Pending-only resolver selection and queue failure behavior | `python3 -m unittest tests.test_resolve_store` |
| Integration | Ephemeral-port stdlib HTTP tests for static allowlist, loopback binding, JSON/Origin/capability-token guards, every endpoint, plan-hash 409 refresh, mtime non-drift, mutation exclusion, gate-free resolve concurrency, instance/state-write/cache-write lock semantics, cross-writer cache contention, Enrich progress/cancel/404, worker-crash release, single-instance refusal, strict pre-scan 503 | `python3 -m unittest tests.test_server` |
| Structural viewer | Toolbar IDs/copy, disabled file mode, CSP `connect-src 'self'`, modal/accessibility markers, fetch routes, job polling, DOM-only rendering, retained template invariants | `python3 -m unittest tests.test_index_assets` |
| Server-side flow | Temporary vault through real HTTP with injected fake operations: resync -> preview -> confirm cleanup -> reload; organize -> preview -> injected collision -> 409 -> reconfirm -> move/re-index; Enrich fake runner -> poll/cancel/404 -> completed/reload | Covered in `tests.test_server`; this does not execute the viewer's JavaScript _(RT#6)_ |
| Regression | Live-root pin test un-pinned first (assert `>= 854` or env-gated), then existing tests plus all new tests | `python3 -m unittest discover -s tests` — currently red (`908 != 854`); the un-pin pre-step lands in Phase 1 before every full-suite gate _(RT#4)_ |
| Manual smoke (blocking phase-exit gate) | Enumerated checklist: 409-refresh re-confirm, focus restoration, timer cleanup, cancel, zero-work plans, Enrich progress/error/restart; optional mechanical `node --check` on the extracted `<script>` when Node exists | `python3 bin/server.py --port 8765` + signed-off checklist before Phase 3 completes _(RT#6)_ |

## Risk assessment

| Risk | Likelihood | Impact | Mitigation / observable trigger |
|---|---|---|---|
| Wrong archive deleted or moved | Low | High | Identity-keyed existing cleanup policy, complete preview, hash-bound snapshot, full preflight before first mutation; any identity drift yields 409/abort |
| Destination collision overwrites a file | Medium | High | Moves use atomically-failing `os.link` + `os.unlink` (`os.rename` silently replaces on POSIX); plan-time detection, planned-to-planned aliases, NFC/case-folded keys, per-move `FileExistsError` handling; collision test preserves destination bytes _(RT#1)_ |
| Concurrent writers corrupt state | Medium | High | Three separated `flock` files: lifetime `server-instance.lock` (single-instance enforcement), operation-held `state-write.lock` across server ops, Enrich `enrich`/`emit` stages, and cooperative CLI writers; `cache-write.lock` with fail-fast against every concurrent cache writer (`resolve`/`spike`/`import-ids`); non-blocking server lock attempts return 409 _(RT#3, RT#5, RT#7)_ |
| OneDrive placeholder hydrates | Low | High | Archive path operations restricted to `stat`/`lstat`/rename/unlink already used by scanner/cleanup; no archive read API in new engine tests |
| Parent-folder version disappears after organization | Certain for affected files | Medium | Explicit per-file warning and total before confirmation; no filename change; cleanup continues preserving unparseable families |
| Resolver takes hours or retries 124 historical misses | High | Medium | Restrict button job to pending queue, run in background, stream bounded progress, preserve resumable per-asset cache writes; `POST /api/enrich/cancel` terminates safely; the gate-free `resolve` stage keeps Resync available _(RT#5)_ |
| Hostile web page reaches destructive localhost POST | Low | High | Loopback bind, no CORS, same-origin `Origin`/`Host` checks, JSON-only POST bodies, per-process capability token with constant-time compare (403 on mismatch), exact accepted keys, no client paths |
| Index refresh fails after a committed mutation | Low | High | Return explicit partial-success state (`mutation_applied`, `state_refreshed`), never claim rollback of deleted files, reconcile from live metadata; Resync stays available (the Enrich `resolve` stage is gate-free, so it cannot wedge the server busy); tests distinguish mutation failure from emit failure _(RT#5)_ |
| Viewer JS runtime defect passes structural tests | Medium | High | Manual smoke promoted to a blocking phase-exit checklist (409 re-confirm, focus, timers, cancel, restart); optional `node --check` mechanical gate; server-side 409 flow covered by real HTTP tests _(RT#6)_ |

## Rollback plan

- **Phase 1 code:** remove the new engine/tests. Before apply, Cancel is lossless. Mid-batch organize failures reverse completed moves. After a successful organize, reverse the previewed `src`/`dst` pairs and run `update`; no automatic undo is added.
- **Phase 2 code:** stop the loopback server, revert server/resolver changes. Resolver cache writes remain valid and resumable. No persistent server job schema exists.
- **Phase 3 code:** revert `HTML_TEMPLATE` and its tests, then run the normal emit command to regenerate `index.html`. CLI workflows remain available throughout.
- **Cleanup data:** deletion is intentionally not rollbackable by this feature. Safety is preview, explicit confirmation, conservative tie policy, and fail-closed preflight; recovery after confirmed deletion requires an external backup/provider restore.

## Success criteria

- [ ] Resync, Organize, and Enrich are visible in the toolbar; `file://` disables all three and shows the server command hint.
- [ ] Resync refreshes the configured vault index without reading Unity Hub, then shows cleanup files, asset identities, reclaimable bytes, and protected ties/unparseable families.
- [ ] Cleanup apply accepts only a still-current confirmed plan; drift returns 409/new preview; successful apply removes only superseded versions and reloads the regenerated viewer.
- [ ] Organize preview groups full `src -> dst` moves by complete category and reports non-store, no-category, already-correct, and collision skips.
- [ ] Organize leaves non-store, no-category, `_Quarantine`, and `_Unresolved` content untouched; target collisions retain their original bytes.
- [ ] Confirmed organize moves preserve basenames, removes only empty former parents, updates the index once, and warns every folder-supplied-version case before apply.
- [ ] Enrich starts one pending-only background job, exposes status/progress/log tail by job ID, keeps unresolved entries pending, and refreshes emitted data on completion.
- [ ] No new dependency; generated-state schemas and existing CLI defaults remain compatible.
- [ ] Full stdlib suite passes after the Phase 1 pre-step un-pins the live-root count test (currently red: `908 != 854`), plus all new unit/integration/structural tests. _(RT#4)_

## Unresolved questions

None. The accepted brainstorm decisions above are binding. Red-team findings are resolved in the review section below.

## Red Team Review

### Session — 2026-09-04
**Findings:** 14 (14 accepted, 0 rejected) from 3 hostile reviewers (Security Adversary, Assumption Destroyer, Failure Mode Analyst) at Standard verification tier (Fact Checker + Contract Verifier / Flow Tracer). ~35 sampled file:line claims verified accurate; the claimed red baseline reproduced live.
**Severity breakdown:** 7 High, 7 Medium

| # | Finding | Severity | Disposition | Applied To |
|---|---------|----------|-------------|------------|
| 1 | POSIX `os.rename` silently overwrites; "never `os.replace`" was a false guarantee — use `os.link`+`os.unlink` | High | Accept | plan, Phase 1 |
| 2 | Hash/preflight bound `mtime_ns`, which OneDrive churns by documented design — identity now `(dev, ino, size)` + manifest | High | Accept | plan, Phases 1-2 |
| 3 | `update`/`enrich`/`emit` are lockless; multi-instance + CLI races could clobber state/queue — shared state-dir lock + single instance | High | Accept | plan, Phase 2 |
| 4 | "234-test baseline passes" already false (live pin test `908 != 854`, verified) — un-pin pre-step added | High | Accept | plan, Phase 1 |
| 5 | Enrich holds the gate for hours with no cancel and blocks Resync — cancel endpoint, planning exempt; final mechanism refined in the advisory round: gate-free `resolve` stage, gate-holding `enrich`/`emit` | High | Accept | plan, Phases 2-3 |
| 6 | Viewer JS covered only by structural assertions, the repo's documented false-pass methodology — blocking smoke gate + optional `node --check` | High | Accept | plan, Phase 3 |
| 7 | Blocking engine `flock` vs busy-409 contract — non-blocking server attempts | Medium | Accept | Phases 1-2 |
| 8 | Enrich worker gate release not exception-bounded — `try/finally` + watchdog + crash test | Medium | Accept | Phase 2 |
| 9 | Non-archive file named like a category ancestor aborts the whole batch opaquely — plan-time `blocked-ancestor` skip class | Medium | Accept | Phases 1, 3 |
| 10 | Resync's lenient scan vs strict downstream agreement — strict pre-scan, clean 503, defined partial-success shape | Medium | Accept | Phase 2 |
| 11 | Static root hardcodes repo path while emit resolves `output_dir` (vault-root fallback) — shared helper + startup assertion | Medium | Accept | Phase 2 |
| 12 | "No-category -> Enrich" is unactionable for resolved-without-breadcrumb assets — split skip classes with accurate copy | Medium | Accept | Phases 1, 3 |
| 13 | `GET /api/job/<id>` undefined for unknown ID/restart — 404 `unknown_job` contract + client terminal transition | Medium | Accept | Phases 2-3 |
| 14 | Case-insensitive/NFD APFS defeats exact-string checks (`_quarantine`->`_Quarantine`; NFC/NFD aliases) — NFC+casefold + realpath containment | Medium | Accept | Phase 1 |

### Whole-Plan Consistency Sweep
- Files reread: plan.md, phase-01-organize-engine.md, phase-02-local-server.md, phase-03-viewer-wiring.md.
- Decision deltas checked: 14 (no-clobber mechanism, mtime-free identity, state-dir lock + single instance, baseline un-pin, Enrich cancel/per-stage locking/planning exemption, blocking smoke + node check, non-blocking flock, finally-bounded worker, blocked-ancestor, strict pre-scan, output_dir helper, no-category split, job 404, NFC/casefold).
- Reconciled stale references: 3 (plan.md "stay put until Enrich resolves them" split per RT#12; duplicate/stale 409 and organize-renderer bullets in Phase 3; missing blank line before this section).
- Unresolved contradictions: 0. Remaining `os.rename`/`mtime`/`234` mentions are corrective or historical (review table, tests, rollback prose).

