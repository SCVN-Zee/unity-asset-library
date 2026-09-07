---
title: Live action progress delivery
date: 2026-09-07
summary: "API v4 jobs, truthful progress panels and verified file-safe lifecycle"
---

# Live action progress delivery

# Live action progress

Implemented plans/260907-0405-action-progress. Resync, Cleanup and Organize preview/apply now use asynchronous API v4 jobs; Enrich retains independent structured progress. Shared panels show real stage counts, current work, elapsed time and retained outcomes.

Integration verification exposed a nested state-flock deadlock, cancellation blocked on locks, rolled-back attempted-move counts, overlapping index/file-removal counters and premature UI-refresh completion. Fixed all while preserving confirmation hashes, native no-download moves and rollback. Failed UI refresh cannot enable reapply.

Proof: 269 Python tests, TypeScript/Vite build and desktop bootstrap test passed. All six real HTTP routes ran against disposable local files: 3 native moves, 2 old-version deletions, real index updates and stale-hash refusal. Real zero-pending Enrich subprocess pipeline passed without online searches. Browser verified concurrent/unknown/known/zero progress, modal state, drift, rollback guidance, cancellation, transient recovery, backend restart, delayed/failed refresh and narrow layout. Real fixture bridge verified Resync completion and index-only removal without claiming disk deletion.

No real asset collection was changed; no commit made. Restart desktop and backend together for API v4. Existing non-failing ResourceWarnings and Vite chunk advisory remain. Verification processes and temporary fixtures/scripts are removed after proof.

> Historical work record — not durable authority. Prefer docs/specs/ADRs for current decisions.
