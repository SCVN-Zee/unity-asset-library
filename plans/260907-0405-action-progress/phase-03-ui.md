---
phase: 3
title: "Shared progress interface"
status: completed
priority: P1
effort: ""
dependencies: []
---

# Shared progress interface

## Goal
Users can see what every action is doing and how much real work has finished.

## Owned files
Modify app/src/App.tsx, app/src/styles.css, app/src/vite-env.d.ts (and create a focused reusable component if useful), electron/main.cjs, electron/preload.cjs, tests/test_desktop_bootstrap.cjs. No backend/test_server edits.

## Steps
1. Update typed bridge: six preview/apply methods return {job_id}; add getAction(id) returning {action: ActionJob}; API compatibility checks bump 3 to 4. State.action and Enrich progress fields follow plan.md. Keep other IPC and external URL guard unchanged.
2. Poll latest action by captured job id every ~900ms, no overlapping requests/out-of-order responses or stale unmounted updates. Recover latest action via getState on renderer load. Enrich polling independent; one action never hides/replaces its status. A backend restart/missing ID reports unknown outcome, not success; retain visible failure guidance.
3. Preview progress starts immediately; terminal plan result opens existing review dialog. Apply keeps dialog, disables dismiss/reconfirm during work and shows same status component visibly inside it. Complete only after real job result and UI data reload; display an index-loading stage if that refresh is still pending. If asset reload fails report UI refresh failure distinctly. No implicit re-apply on drift: show replacement preview and require user confirmation again.
4. Reuse a progress panel at existing job-line/summary area. Show action/stage, current file, elapsed time, determinate stage percent only if total is known, factual processed/moved/skipped/deleted counters and accessible native progress semantics. Stage percent is not overall percent. Unknown totals show discovered count and spinner/indeterminate bar; zero total never divides by zero. Do not display 100% overall during final index refresh.
5. Retain dismissible success/failure/cancelled summaries. Dismiss is local by operation ID so old state polling cannot resurrect it. Enrich cancel has cancelling acknowledgement, existing backend cancellation only. Preserve error text, drift distinctions, rollback stage and applied-but-index-refresh-failed notice. Do not invent Undo action.
6. Product preserve-mode: existing dark surfaces, typography, spacing, radius and accent; no new visual system. Accessible live region for stage changes (avoid per-file screenreader flood), reduced motion, tabular counts, truncated current path with accessible full value, narrow viewport wraps controls rather than squeezing text.

## Verification
npm run build and node --test tests/test_desktop_bootstrap.cjs. Main will visually exercise Vite bridge scenarios for unknown/determinate, apply dialog, failed/rollback/drift/cancelled, concurrent jobs, narrow window. Do not add source-string UI tests. Send component selectors and dev startup command for browser smoke.

Completed: shared retained panels, exact phase labels, stage counts/current item/timer, independent polling and restart uncertainty. Browser verified unknown/known/zero totals, concurrent jobs, apply modal, drift reconfirmation, cancellation, transient polling recovery, delayed/failed library refresh, and 1000px layout. Real fixture backend Resync preview/apply completed through the renderer; modal closed only after asset reload. Typecheck/build and desktop bootstrap check pass.
