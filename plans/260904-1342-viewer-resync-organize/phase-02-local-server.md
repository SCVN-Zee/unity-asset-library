---
phase: 2
title: "Local Server"
status: completed
priority: P1
effort: "9h"
dependencies: [1]
---

# Phase 2: Local Server

## Overview

Add a loopback-only stdlib HTTP server that serves the generated viewer, exposes fixed-path JSON operations, binds confirmations to fresh filesystem snapshots, serializes mutations, and runs pending-only store resolution in a pollable background job.

## Requirements

**Functional**

- Serve only `/` and `/index.html` plus `/assets.csv`, resolved through the same `output_dir` resolution as `emit` (one shared helper in `index_assets.py`; startup assertion refuses to start when the configured emit target is not the served directory); no directory listing or arbitrary path mapping. _(RT#11)_
- Add JSON endpoints:

| Method/path | Behavior | Success |
|---|---|---|
| `GET /api/state` | Pending-enrichment count, server readiness, current/latest job summary, and the per-process capability token | 200 |
| `POST /api/resync` | Strict vault pre-scan (walk error = clean 503, zero writes); run configured-vault `update`, strict scan, cleanup re-plan, snapshot/hash, return preview with explicit `state_refreshed` status | 200 / 503 |
| `POST /api/cleanup/apply` | Accept only `plan_hash`; rescan/replan/rehash; 409 on drift; otherwise existing cleanup apply | 200 |
| `POST /api/organize/plan` | Load server-owned state, validate/live-plan through Phase 1 engine, return preview/hash | 200 |
| `POST /api/organize/apply` | Accept only `plan_hash`; replan/rehash; 409 on drift; otherwise organize apply | 200 |
| `POST /api/enrich/start` | Start one pending-only background pipeline and return job ID | 202 |
| `POST /api/enrich/cancel` | Terminate the tracked child process, mark the job `cancelled`, release locks; safe because cache writes are per-asset atomic | 200 / 409 |
| `GET /api/job/<id>` | Return `queued/running/completed/failed/cancelled`, stage, counts, bounded log tail, error; unknown ID returns 404 `{error: "unknown_job"}` | 200 / 404 |

- A drift response is `{error: "plan_changed", plan, plan_hash}` with HTTP 409. The client must show the replacement plan and require another click; never auto-apply it.
- A busy mutation endpoint returns HTTP 409 `{error: "busy", job?: ...}` rather than waiting behind a gate-holding operation. Only the Enrich `enrich`/`emit` stages and active applies can hold the mutation gate; the hours-long `resolve` stage is gate-free and never causes busy responses. Server attempts on the engine `flock` are non-blocking (`LOCK_EX | LOCK_NB`); `BlockingIOError` maps to the same 409. _(RT#7)_
- Add pending-only resolution to the existing CLI: `resolve_store.py resolve --resume --pending`. Without `--pending`, current full eligible-set behavior remains unchanged.
- Enrich pipeline: pending-only resolve -> existing cache merge/queue prune -> emit current viewer. Expose progress while resolution runs and remaining pending count at completion.
- Default bind is `127.0.0.1:8765`; `--port` is the only network option. Do not add a host flag. The server lifetime-holds `server-instance.lock` (non-blocking `flock` in the state dir; a second instance refuses startup on any port). This instance lock is distinct from the operation-held `state-write.lock`, so lifetime enforcement never starves CLI/subprocess writers. _(RT#3)_

**Non-functional**

- Stdlib server/client primitives only: `http.server`, `threading`, `subprocess`, `uuid`, `deque`, `hashlib`, `json`.
- No client-controlled root, state path, executable, command, archive path, plan rows, destination, log length, or static filename.
- Require JSON content type for POST, cap request bodies, reject unknown/missing keys, validate `Host`, and reject a present cross-origin `Origin`; emit no CORS allowance.
- Add `Cache-Control: no-store` to API and generated static responses so reload observes a completed re-index.
- Keep job memory bounded: one active/latest job per server instance and a fixed-size log deque.
- Server errors must identify whether no filesystem mutation occurred, moves/deletions committed, or only the final index refresh failed.

## Architecture

### Lifetime and concurrency

Create one `ViewerService` [NEW] for each `ThreadingHTTPServer` [NEW] instance. `main()` is its sole production instantiation; `tests/test_server.py` creates isolated instances with fake operation/subprocess seams. The service owns:

- immutable repo/state/config-derived paths;
- one non-reentrant mutation lock shared by every POST operation;
- one bounded latest-job record and a small lock protecting job snapshots;
- injected command runner/operation callables for tests, defaulting to real modules.

This state is per server process, not per request. Handler instances never own locks or jobs, preventing concurrent request threads from bypassing serialization.

Three named `flock` files in the state dir (one small helper) keep concerns separate. `server-instance.lock` is lifetime-held and enforces single instance. `state-write.lock` is operation-held and serializes every state writer — server Resync, both apply endpoints, and the Enrich `enrich`/`emit` stages; server attempts are non-blocking (`LOCK_EX | LOCK_NB`, busy 409 on `BlockingIOError`), while cooperative CLI runs of `update`/`enrich`/`emit` acquire it blocking. `cache-write.lock` is cache-write-lifetime-held by every cache-mutating CLI command — the shared `resolve`/`spike` flow (`bin/resolve_store.py:935-945`, per-asset whole-snapshot save at `1016-1025`) and `import-ids` (loads once at `877`, whole-snapshot save at `907`) — acquired inside the resolver CLI so manual and server-launched runs share one path, failing fast with a clear error on contention; it exists because each writer rewrites the whole in-memory `cache.json` snapshot, so two concurrent writers would silently drop each other's newer entries despite atomic replacement (`enrich` only reads cache, so it needs no lock). The network `resolve` stage is job-exclusive and takes no mutation gate or `state-write.lock` — only `cache-write.lock` — so Resync and organize planning stay available for its whole duration. The `enrich` and `emit` stages each acquire the mutation gate and `state-write.lock`, blocking worker-side until any in-flight Resync/apply finishes; server endpoints stay non-blocking (busy 409), so a Resync arriving mid-`enrich` gets 409 with the job ID and can retry after the seconds-long stage ends. Destructive engines keep the existing vault `flock` (`bin/cleanup_versions.py:167-176`), acquired non-blocking in server paths. Read-only planning (organize plan, cleanup preview shaping) is exempt from the mutation gate. _(RT#3, RT#5, RT#7)_

### Approval hash

The browser receives a normalized human preview and opaque `plan_hash`, never a trusted operation list. Build the hash with canonical JSON (`sort_keys=True`, compact separators) and SHA-256 over:

- operation discriminator (`cleanup` or `organize`);
- every normalized move/removal, skip/warning summary, and total shown to the user;
- root identity and a deterministic fingerprint of the path manifest and `(dev, ino, size)` archive identities — deliberately excluding `mtime_ns`, which OneDrive rewrites as a matter of course (`bin/index_assets.py:1628-1632`); mtime-only churn therefore never produces a 409 _(RT#2)_
- destination occupancy/ancestor fingerprint for organize.

Apply rebuilds that material from current server-owned state. Inode/size change, added/removed archive, category change, or new collision changes the hash; a rewritten `mtime_ns` alone does not. A mismatch returns a complete replacement preview. A race after the comparison still hits the engine preflight: cleanup already revalidates snapshot/root under its lock (`bin/cleanup_versions.py:211-252`), and Phase 1 does the same for organize. _(RT#2)_

### Endpoint flows

**Resync:**

1. Acquire the mutation gate (non-blocking; 409 busy with the job ID if an Enrich `enrich`/`emit` stage owns it — the gate-free `resolve` stage never blocks Resync); call existing `index_assets.main(["update"])`. Its real flow rescans, rebuilds, merges cache/overrides, writes state, queues changes, and emits outputs (`bin/index_assets.py:1815-1903`).
2. Strictly scan current root, run `cleanup_versions.plan_cleanup`, capture its full archive snapshot, shape only actionable/protected summary rows, hash, release gate, return plan.
3. Cleanup apply repeats step 2 before comparison. On match, call `apply_cleanup` with the same freshly captured scan/snapshot. Its cleanup policy and single update handoff remain untouched (`bin/cleanup_versions.py:39-70`, `bin/cleanup_versions.py:211-294`).

**Organize:**

1. Acquire mutation gate; read `state/assets.json`, strict-scan root, and call Phase 1's planner/snapshot builder.
2. Shape category-grouped moves plus skip summaries and folder-version warnings; hash and return.
3. Apply repeats all reads and planning. On match, pass only the fresh server-side objects into `apply_organize`; client JSON never becomes filesystem input.

**Enrich:**

1. `POST /api/enrich/start` creates `uuid.uuid4().hex`, records `queued`, starts one daemon worker, and returns 202; start is refused with 409 `{busy}` while another job is active (job-exclusive). The `resolve` stage takes no mutation gate or `state-write.lock` — only the resolver CLI's own `cache-write.lock` — and writes only `cache.json`. `POST /api/enrich/cancel` terminates the tracked `Popen` (per-asset atomic cache writes make termination safe — `bin/resolve_store.py:1016-1025`), records `cancelled`, and releases every lock the job holds. _(RT#5)_
2. Worker launches fixed argv with `sys.executable -u`: `bin/resolve_store.py resolve --resume --pending`. Stream merged stdout/stderr line-by-line into `deque(maxlen=200)`; parse existing `resolve: N assets to attempt` and `[i/N]` output (`bin/resolve_store.py:955-1025`) into total/current counts without making parsing a success condition.
3. On exit 0, run fixed `bin/resolve_store.py enrich`, then fixed `bin/index_assets.py emit`. Each stage acquires the mutation gate and `state-write.lock`, blocking in the worker until free (a concurrent Resync briefly delays it; the reverse never happens), and releases on exit. Existing `enrich` merges cache and prunes only resolved queue entries (`bin/resolve_store.py:914-933`); `emit` refreshes HTML/CSV/review queue (`bin/index_assets.py:1892-1903`). _(RT#5)_
4. The worker body is `try/finally`-bounded: gate and `state-write.lock` release happen on every exit path, including `Popen`/parse exceptions, and a watchdog force-fails a `running` job whose child has exited without a terminal transition. Any nonzero stage sets `failed`, preserves its log/error, and skips later stages. Success records remaining queue count and `completed`. _(RT#8)_
5. Graceful server shutdown terminates/waits for the owned child process; cache remains resumable because resolution writes each result atomically (`bin/resolve_store.py:474-476`, `bin/resolve_store.py:1016-1025`).

### Pending-only resolver cutover

The parser already accepts `--pending`, but currently uses it only for `export-titles` (`bin/resolve_store.py:823-865`). Extract the queue read into one helper and reuse `filter_worklist`, whose `pending_keys` parameter already narrows and excludes non-store assets (`bin/resolve_store.py:483-493`). Before constructing `todo` at `bin/resolve_store.py:935-945`, narrow `eligible` when `args.pending`; then retain `--resume`'s resolved-record exclusion. The shared `resolve`/`spike` flow and `import-ids` additionally lifetime-hold `cache-write.lock` (non-blocking, fail-fast with a clear error and nonzero exit when any other cache writer already holds it), because whole-snapshot rewrites would silently drop a concurrent writer's entries. Missing/empty/malformed queue resolves zero assets with a clear log, never falls back to the full historical retry set.

### HTTP boundary

A small `BaseHTTPRequestHandler` subclass [NEW] performs routing/encoding only:

- fixed GET allowlist; query strings do not select files;
- maximum JSON body (for example 16 KiB), exact object schemas (`{csrf}` or `{plan_hash, csrf}` — every mutating POST carries the capability token), and `application/json` requirement;
- `Host` limited to `127.0.0.1:<bound-port>` / `localhost:<bound-port>`;
- if `Origin` is present, require the same loopback origin; cross-site form/content types fail before an operation runs;
- every mutating request must carry the per-process capability token issued in the `/api/state` payload (`secrets.token_hex(32)`, constant-time compare; missing/wrong = 403) — defense-in-depth for cross-site request paths the Host/Origin/content-type checks may not cover in nonstandard clients, and against future endpoint mistakes;
- consistent JSON errors and `Content-Length`; `404 {"error": "unknown_job"}` for unknown job IDs; no stack traces or absolute vault paths in responses. _(RT#13)_

## Related Code Files

- Create: `bin/server.py` - service, confirmation hashing, job lifecycle, fixed static/API handler, CLI.
- Create: `tests/test_server.py` - real ephemeral loopback HTTP requests with fake operations and temporary state.
- Modify: `bin/resolve_store.py:483-518`, `bin/resolve_store.py:813-865`, `bin/resolve_store.py:935-1025` - reusable pending-key load and pending-only resolve scope.
- Modify: `tests/test_resolve_store.py:878-944` - CLI/worklist regression coverage.
- Consume, do not modify: `bin/cleanup_versions.py:39-70`, `bin/cleanup_versions.py:139-176`, `bin/cleanup_versions.py:211-315`.
- Consume, do not modify: `bin/organize_versions.py` [NEW in Phase 1].
- Consume, do not modify: `bin/index_assets.py:1783-1904`.

## Implementation Steps

### Tests Before

1. Extend pending-worklist tests around `tests/test_resolve_store.py:878-944`:
   - `resolve --pending --resume` attempts queued unresolved assets only;
   - already-resolved queued entries stay excluded by `--resume`;
   - empty/missing/malformed queue attempts zero and never widens to all eligible assets;
   - no `--pending` preserves the current full eligible set;
   - non-store remains excluded even if queued.
2. Create an ephemeral-server fixture using `ThreadingHTTPServer(("127.0.0.1", 0), ...)`, a background serve thread, `http.client`, temporary static files, and a fake `ViewerService`/runner. Cleanly `shutdown`, `server_close`, and join in teardown.
3. Write static/boundary tests: exact bind address, three allowed static routes, MIME/length/no-store headers, 404 for traversal/unknown routes, JSON-only POST, body limit, key rejection, bad Host/Origin rejection, missing/wrong capability token = 403 with zero operations, and no CORS header.
4. Write operation tests:
   - Resync runs a strict pre-scan (injected walk error = clean 503, zero writes) before update and returns an explicit `state_refreshed` status;
   - cleanup apply re-plans; matching hash calls existing apply once;
   - altered removal, same-size source inode, or root snapshot returns 409/new plan and makes zero apply calls; an `mtime_ns`-only change returns 200 (non-drift);
   - organize plan groups category rows and organize apply uses only server-produced objects;
   - destination appearance/category change returns 409 and makes zero move calls.
5. Write concurrency/job tests:
   - Enrich returns 202/job ID immediately;
   - second Enrich returns busy 409 while a job is active; Resync/organize/cleanup succeed during the `resolve` stage (gate-free) and return busy 409 with the job ID only while an `enrich`/`emit` stage or apply holds the gate;
   - job polling observes queued/running/progress/completed and at most 200 log lines;
   - resolve failure skips enrich/emit and records failed;
   - success executes exactly `resolve --resume --pending`, `enrich`, `emit` in order and reports remaining count;
   - shutdown terminates an active fake child and releases resources.
   - cancel terminates the fake child mid-run, records `cancelled`, releases the gate, and later mutations succeed;
   - a worker crash between stages releases the gate via the finally/watchdog path (next mutation is not busy-409);
   - unknown job ID returns 404 `unknown_job`; a restarted service object returns it for a stale job ID;
   - a second server instance refuses startup while `server-instance.lock` is held, on any port, and `state-write.lock` remains acquirable by CLI writers throughout;
   - an external CLI holder of the engine flock makes server apply return busy 409 immediately (non-blocking attempt);
   - a manual CLI `import-ids` holding `cache-write.lock` makes a second cache writer (server resolve job or `resolve`/`spike`/`import-ids` CLI) fail fast with a clear error and zero cache writes, and vice versa;
   - `output_dir`/emit-target divergence fails startup with a clear error.

### Refactor / Implementation

6. Factor pending-queue loading in `resolve_store.py`; apply the filter before existing resume selection without changing the default path or retry/cache semantics.
7. Implement `ViewerService` path resolution, plan shaping, snapshot-bound canonical hash, nonblocking mutation gate, and latest-job snapshots. Keep plan hashing and filesystem apply in the service, not the HTTP handler.
8. Implement the fixed static/API handler with request guards and explicit status mapping: 200/202 success, 400 malformed input, 404 unknown, 409 busy/drift/safety re-plan, 415 wrong content type, 500 operation failure with mutation state.
9. Implement the subprocess job runner and bounded progress parser. Never invoke a shell; every argv/path is code-defined from `index_assets.repo_dir()` (`bin/index_assets.py:1783-1785`).
10. Implement `main(argv)` with `--port`, fixed `127.0.0.1`, startup URL, Ctrl-C shutdown, child cleanup, and no browser auto-open.

### Tests After

11. Run `python3 -m unittest tests.test_resolve_store tests.test_server`.
12. Start the server against the repository and smoke only non-destructive paths: GET index/state, Resync preview, Organize preview, invalid-hash 409, and server shutdown. Do not confirm live deletion/moves during this phase.

## Success Criteria

- [ ] Server listens only on `127.0.0.1`, defaults to port 8765, exposes no host override, and enforces single instance via the lifetime-held `server-instance.lock`, distinct from `state-write.lock` and `cache-write.lock`. _(RT#3)_
- [ ] Only generated viewer/CSV and specified APIs are reachable; traversal and arbitrary repo/vault files are not served.
- [ ] POST boundary rejects cross-origin/non-JSON/oversized/unknown-key/missing-or-wrong-token requests before any operation.
- [ ] Resync runs index update before returning a cleanup preview from a strict fresh scan.
- [ ] Both apply endpoints derive paths server-side and return 409 plus a new preview on any hash/snapshot drift; mtime-only churn never drifts. _(RT#2)_
- [ ] A server-lifetime mutation gate serializes Resync, organize, cleanup, and the Enrich `enrich`/`emit` stages; the gate-free `resolve` stage never blocks Resync or planning; job polling remains available while busy. _(RT#5)_
- [ ] Enrich returns immediately, attempts only pending unresolved store assets, streams bounded progress, merges/prunes/emits on success, fails visibly without launching later stages, and is cancellable mid-run with all locks released; Resync can run throughout the `resolve` stage and only briefly busy-blocks on `enrich`/`emit`. _(RT#5)_
- [ ] Existing resolver behavior without `--pending`, cache format, cleanup policy, and index CLI remain unchanged.
- [ ] Targeted stdlib resolver/server tests pass; non-destructive live server smoke passes.
- [ ] Static roots resolve through the shared `output_dir` helper with a startup divergence check; unknown job IDs return 404; worker crashes cannot wedge the mutation gate. _(RT#8, RT#11, RT#13)_
- [ ] Every mutating POST requires the per-process capability token; a missing or wrong token is a 403 with zero operations performed.
- [ ] `state-write.lock` and `cache-write.lock` are distinct from the lifetime instance lock; a second cache writer (`resolve`/`spike`/`import-ids` or server job) fails fast with zero cache writes and a held `cache-write.lock` never blocks Resync or planning.

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation / trigger / response |
|---|---|---|---|
| Malicious site submits a localhost destructive request | Low | High | Loopback bind, Host/Origin checks, JSON-only exact bodies, per-process capability token with constant-time compare (403 on mismatch), no CORS; rejected-request tests prove operation count remains zero |
| Browser confirms a stale plan with same path/size | Medium | High | Hash includes `(dev, ino, size)` identities + manifest, not display rows alone; inode/size change returns 409; mtime churn excluded by design _(RT#2)_ |
| Request handlers each receive a separate lock | Low | High | Locks live on one per-server `ViewerService`; lifetime `server-instance.lock` for instance enforcement; lifetime/instantiation tests issue concurrent real HTTP requests _(RT#3)_ |
| Long Enrich blocks or queues destructive requests invisibly | High | High | Nonblocking gate returns busy 409 with job ID; the gate-free `resolve` stage keeps Resync and planning available for its whole duration; only the brief `enrich`/`emit` stages busy-block; `POST /api/enrich/cancel` ends the job safely; read-only planning stays available _(RT#5, RT#7)_ |
| Malformed/missing queue accidentally triggers full retry sweep | Medium | High | `pending_keys=set()` on failure, never `None`; test asserts zero resolver calls |
| Two cache writers run concurrently (server job + manual CLI `resolve`/`spike`/`import-ids`) | Low | Medium | `cache-write.lock` fail-fast on every cache-mutating path (acquired inside the resolver CLI); contention regression test proves the second writer writes nothing |
| Subprocess becomes orphaned on normal shutdown | Low | Medium | Track active `Popen`; terminate/wait in `finally`; per-item cache writes make restart safe |
| Progress parser stops matching changed resolver copy | Medium | Low | Raw bounded log remains authoritative; status follows process exit; parser mismatch yields unknown counts, not job failure |
| Apply commits data change but final emit fails | Low | High | Response carries `mutation_applied`/`state_refreshed`; never retry mutation automatically; Resync reconciles and cannot be wedged busy by the gate-free `resolve` stage (only `enrich`/`emit` briefly busy-block; cancel available); tests distinguish the failure modes _(RT#5, RT#10)_ |
| Static page reload returns cached pre-update HTML | Medium | Medium | `Cache-Control: no-store` on static/API responses; end-to-end reload assertion |
| Worker crash wedges the gate at busy-409 | Low | High | `try/finally`-bounded release plus watchdog on exited-child-running state; crash-between-stages test asserts the next mutation succeeds _(RT#8)_ |

## Rollback Plan

Stop the server to remove the HTTP surface. Revert/delete `bin/server.py` and `tests/test_server.py`, then revert the additive pending-resolve changes and tests. Existing CLI cleanup, organize, and full resolver flows remain usable. Valid cache records written by a stopped Enrich job are retained; the current resolver's `--resume` semantics safely continue them (`bin/resolve_store.py:935-944`). No durable server job state requires migration or cleanup.
