---
phase: 3
title: "Viewer Wiring"
status: completed
priority: P1
effort: "6h"
dependencies: [1, 2]
---

# Phase 3: Viewer Wiring

## Overview

Wire Resync, Organize, and supporting Enrich controls into the generated single-file viewer. Add server-readiness gating, accessible plan previews, confirmation/drift handling, background progress, and reload after regenerated output without weakening existing viewer security/rendering invariants.

## Requirements

**Functional**

- Add visible `Resync`, `Organize`, and `Enrich` buttons to the existing toolbar at `bin/index_assets.py:856-869`.
- Render all action buttons disabled by default. On `file://`, keep disabled and show “Run `python3 bin/server.py` to enable library actions.” Over HTTP, enable only after `GET /api/state` succeeds.
- Show pending count in the Enrich label/state and disable Enrich while a job is active. Mutation buttons stay enabled while the Enrich `resolve` stage runs (it is gate-free) and disable only while an apply is in flight or an `enrich`/`emit` stage holds the gate; the server busy-409s conflicting requests regardless. _(RT#5)_
- Resync click shows busy state while `POST /api/resync` runs, then opens a cleanup plan with removal files, asset identities, survivor, size, reclaimable total, and protected tie/unparseable counts.
- Cleanup confirmation sends only `plan_hash`. Cancel after Resync reloads the already-refreshed non-destructive index; successful apply reloads after server re-index.
- Organize click opens a category-grouped `src -> dst` move preview, aggregate move totals, skip summaries (`non-store`; `no-category` split into "pending — Enrich can resolve" vs "resolved without breadcrumb — needs an `overrides.json` entry"; `already-correct`; `blocked-ancestor` with the offending path; `collision`), collision rows, and folder-supplied-version warnings. Filenames are never changed. _(RT#9, RT#12)_
- Organize confirmation sends only `plan_hash`; successful apply reloads. Cancel closes without a mutation.
- Enrich click starts the background job, polls by returned job ID, displays stage/current/total/remaining plus bounded log tail, handles failure, and reloads on completion. A Cancel control calls `POST /api/enrich/cancel`; read-only planning previews stay available because the server exempts them from the mutation gate, and Resync stays usable throughout the hours-long `resolve` stage. _(RT#5)_
- HTTP 409 `plan_changed` replaces the displayed preview/hash, announces drift, and requires a fresh explicit confirmation. HTTP 409 `busy` links status to the active job and performs no automatic retry.
- A zero-removal or zero-move plan is still legible but has no destructive Confirm action.

**Non-functional**

- Keep `HTML_TEMPLATE` self-contained, vanilla JS, responsive, keyboard accessible, and compatible with the existing light/dark/reduced-motion system (`bin/index_assets.py:686-852`).
- Extend CSP only with `connect-src 'self'`; retain the existing exact image/script/style restrictions at `bin/index_assets.py:689-704`.
- Render server and plan data only through DOM creation and `textContent`; the existing suite forbids `.innerHTML` anywhere in the template (`tests/test_index_assets.py:417-420`).
- Preserve existing toolbar/view/density/filter behavior and event wiring (`bin/index_assets.py:857-930`, `bin/index_assets.py:1542-1593`).
- Do not add a browser/test dependency to the shipped artifact or suite. Follow the established `HTML_TEMPLATE` structural assertion style (`tests/test_index_assets.py:315-420`, `tests/test_index_assets.py:519-584`, `tests/test_index_assets.py:977-1030`), and close the documented structural-test blind spot with a blocking manual smoke gate plus an optional mechanical `node --check` of the extracted `<script>` when Node is available (`docs/journals/2026-07-31-index-viewer-redesign.md`). _(RT#6)_
- Never edit root `index.html` by hand; regenerate it through `index_assets.py emit` (`bin/index_assets.py:1618-1619`, `bin/index_assets.py:1892-1903`).

## Architecture

### Static-safe startup

Add action buttons with the native `disabled` attribute and an `#actionstatus` live-status element. Startup branches before any request:

1. `location.protocol === "file:"`: leave disabled and render the server command hint.
2. HTTP(S): call same-origin `GET /api/state`.
3. State success: cache pending/job summary, enable appropriate controls, and render `Enrich (N)`.
4. State failure or wrong static server: keep disabled and show the same server hint/error. Never “optimistically” enable destructive controls.

The template currently reads embedded data and initializes all UI in one IIFE (`bin/index_assets.py:881-909`); action state stays in that same closure. No second application state layer is introduced.

### API client

Add one small `api(method, path, body)` [NEW] wrapper around `fetch`:

- same-origin relative paths only;
- `Content-Type: application/json` for POST; include the capability token from the `/api/state` payload in every POST body (`{csrf}` or `{plan_hash, csrf}`), defaulting to `{}` plus token when there are no other arguments;
- parse the server JSON body for both success and error status;
- throw/return a typed object preserving status, `error`, replacement `plan`, and `job`;
- no retry. A destructive confirmation is never replayed automatically.

`connect-src 'self'` is necessary because current CSP is `default-src 'none'` (`bin/index_assets.py:691`). It does not allow any remote API host.

### Action modal

Use one reusable static modal shell [NEW], populated with `el()` and `textContent`; `el()` is the existing safe DOM helper at `bin/index_assets.py:908-909`.

- `role="dialog"`, `aria-modal="true"`, labelled title/description, hidden when closed.
- Opening stores the triggering button, focuses Cancel/title, and traps Tab between modal controls.
- Escape closes the modal before the current search/detail Escape behavior at `bin/index_assets.py:1542-1551`; closing restores trigger focus.
- Confirm button label states the operation and count: `Delete N superseded files` or `Move N archives`.
- Disable all action controls while a request is in flight; restore from `/api/state` after recoverable error.

**Cleanup renderer:** summary strip for file count, identity count, reclaimable bytes; table rows keyed by identity with survivor/removal path/version/size; protected exact-tie and unparseable totals; warning that deletion cannot be undone by the viewer.

**Organize renderer:** one section/table per category; `src -> dst` rows with size; skip summary for non-store/no-category/already-correct/collision; collision detail rows; prominent list/count of moves that lose a folder-supplied version; copy states filenames are not changed.

All paths are displayed as inert text. No link, `file://` navigation, HTML parsing, or command construction uses them.

### Resync state transition

`idle -> resyncing -> preview` after the server has already refreshed generated files. From preview:

- Confirm -> `applying -> success -> location.reload()`.
- Cancel -> close and `location.reload()` so the page consumes the non-destructive refresh.
- No candidates -> modal explains “Index refreshed; nothing superseded,” then reload on Close.
- 409 plan changed -> stay in preview, replace rows/hash, announce “Vault changed; review the refreshed plan,” and restore Confirm.
- Failure before refresh -> keep current page and show error. Failure after mutation -> show server's explicit applied/reconciliation state; never silently retry.

### Organize state transition

`idle -> planning -> preview -> applying -> reload`. Cancel returns to idle without reload. A 409 replacement plan resets the Confirm button and focus. No-category summary includes an Enrich action only by focusing/highlighting the existing toolbar button; the modal does not start network work implicitly.

### Enrich state transition

`idle -> starting -> queued/running -> completed|failed`:

- Poll `GET /api/job/<id>` on a single timer (approximately one second); treat a 404 `unknown_job` response or consecutive poll failures as a terminal `interrupted` state — re-fetch `/api/state`, re-enable controls, and show "job interrupted by server restart — cache is resumable, re-run Enrich". Clear the timer on any terminal status or page unload. _(RT#13)_
- Show stage and numeric progress when available; raw log tail goes in a scrollable `<pre>` via `textContent`.
- While an `enrich`/`emit` stage or apply holds the gate, destructive controls are disabled; during the `resolve` stage they remain enabled. Filtering/search/detail behavior is always usable.
- Completed: show resolved/remaining counts, then reload to consume emitted data.
- Failed: retain log/error, stop polling, re-fetch `/api/state`, and offer the same Enrich button for a user-driven retry. No automatic restart.

## Related Code Files

- Modify: `bin/index_assets.py:686-1598` - CSP, action markup/styles, modal, API client, state transitions, polling.
- Modify: `tests/test_index_assets.py:315-420`, `tests/test_index_assets.py:519-584`, `tests/test_index_assets.py:952-1030`, `tests/test_index_assets.py:1102-1121` - structural contracts and helper reuse.
- Regenerate: `index.html` - generated from current state through `bin/index_assets.py:1618-1619` and the emit branch at `bin/index_assets.py:1892-1903`.
- Consume, do not modify: `bin/server.py` [NEW in Phase 2] - endpoint contract.
- Consume, do not modify: `bin/organize_versions.py` [NEW in Phase 1] - plan fields shown by the modal.

## Implementation Steps

### Tests Before

1. Add a `TestViewerActions` structural group to `tests/test_index_assets.py` before template edits:
   - unique buttons/IDs and visible labels for Resync, Organize, Enrich;
   - buttons carry initial `disabled`; startup checks `location.protocol` and includes exact server command hint;
   - `/api/state`, all POST routes, and `/api/job/` appear as relative paths;
   - POST requests set JSON content type, carry the capability token from `/api/state`, and send only `plan_hash` alongside it on apply;
   - `connect-src 'self'` exists while CDN image and inline-only script/style directives remain unchanged;
   - no `.innerHTML` regression and all dynamic plan/log fields reach `textContent`/`el`.
2. Add structural dialog/state assertions:
   - dialog ARIA/focus restoration/Escape-first markers;
   - cleanup renderer includes files, identities, reclaimable bytes, protected ties/unparseable copy;
   - organize renderer groups category, prints source/destination, all skip labels including the split no-category copy and `blocked-ancestor`, and folder-version warning;
   - `plan_changed` branch replaces stored hash and never recursively invokes apply;
   - zero-work plans disable/omit destructive confirmation;
   - one polling timer is cleared on terminal status/failure/unload and completion calls reload;
   - Enrich cancel control calls `/api/enrich/cancel`; the job panel renders `cancelled`/`interrupted` and re-enables controls;
   - the unknown-job/404 branch transitions to `interrupted`, re-fetches `/api/state`, and re-enables controls without reloading mid-poll.
3. Preserve existing brittle contracts while writing tests: safe JSON injection (`tests/test_index_assets.py:315-385`), exact CSP host checks (`tests/test_index_assets.py:387-415`), DOM-only rendering (`tests/test_index_assets.py:417-420`), category hierarchy strings (`tests/test_index_assets.py:536-550`), and existing template helper parsing (`tests/test_index_assets.py:1102-1121`).

### Refactor / Implementation

4. Extend existing tokens/styles for disabled buttons, action status, modal backdrop/panel, summary rows, grouped tables, warning/error text, and progress log. Reuse `--surface`, `--line`, `--accent`, `--warn`, and radius tokens; add no second theme.
5. Add disabled-by-default controls and modal shell beside the existing toolbar/main markup. Keep browsing controls independent from mutation disabled state.
6. Implement the API wrapper, startup state fetch, centralized `setActionsEnabled`/status rendering [NEW], and error normalization inside the existing IIFE.
7. Implement one modal controller and the two operation-specific DOM renderers. Integrate modal precedence into the existing global keydown handler without changing search/detail keyboard behavior when closed.
8. Wire Resync and Organize click/confirm/cancel transitions exactly to the Phase 2 endpoints. For 409, replace the preview and require the user to confirm again.
9. Wire Enrich start/poll/final state. Bound displayed log to the server response and never accumulate duplicate timers/client log copies.
10. On successful cleanup/organize/Enrich, reload only after the server reports its final emit stage complete.

### Tests After

11. Run `python3 -m unittest tests.test_index_assets`.
12. Regenerate checked-in output with `python3 bin/index_assets.py emit`; verify root `index.html` contains the new controls and same embedded asset payload shape.
13. Run `python3 -m unittest discover -s tests`; the live-root pin test was un-pinned in Phase 1, so the full suite (existing plus new) must be green.
14. Launch `python3 bin/server.py --port 8765` and complete the BLOCKING manual smoke checklist — phase exit requires every box signed off:
   - HTTP page enables controls after state load; direct `file://` copy shows all three disabled plus hint;
   - Resync preview and Cancel reload;
   - cleanup/organize zero-work and populated modal layouts, keyboard focus, warnings, skip/collision summaries;
   - invalid hash produces refreshed 409 preview without apply;
   - fake/test Enrich job shows progress, terminal failure, and completion reload.
   - Enrich cancel mid-run; kill/restart the server mid-job and confirm the viewer reaches `interrupted` and re-enables controls;
   - Resync completes while a fake `resolve` stage runs (gate-free concurrency); a busy-409 notice appears only if attempted during `enrich`/`emit`;
   - when Node is available, `node --check` passes on the extracted `<script>` block.

## Success Criteria

- [ ] Three action buttons are visible in the existing toolbar without breaking view/density/filter controls.
- [ ] `file://` and an HTTP server lacking `/api/state` leave actions disabled and show `python3 bin/server.py`; the loopback server enables them.
- [ ] Resync performs the non-destructive refresh before showing exact cleanup files, identities, reclaimable bytes, and protected-family counts.
- [ ] Cleanup applies only after explicit modal confirmation; Cancel still reloads the refreshed index; 409 requires review/reconfirm.
- [ ] Organize preview is grouped by full category and lists every move plus non-store/no-category/already-correct/collision skips.
- [ ] Folder-supplied-version warnings and no-rename consequence are visible before organize confirmation.
- [ ] Enrich exposes pending count, starts once, polls status/progress/log, supports cancel, handles server-restart/unknown-job as a terminal `interrupted` state, keeps destructive controls enabled during the gate-free `resolve` stage while disabling them under the gate, and reloads only after merge/prune/emit. _(RT#5, RT#13)_
- [ ] Dialog is keyboard operable, Escape-safe, focus-restoring, and uses only DOM/text APIs for dynamic server content.
- [ ] CSP permits only same-origin API connections in addition to its existing restrictions.
- [ ] `index.html` is regenerated by the pipeline; targeted and full stdlib suites pass.
- [ ] The manual smoke checklist is fully signed off (and `node --check` passes where Node exists) before Phase 3 closes. _(RT#6)_

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation / trigger / response |
|---|---|---|---|
| Buttons appear active in a static viewer and fail destructively/confusingly | Medium | High | Native disabled markup; enable only after state success; file/wrong-server structural and manual checks |
| 409 response is auto-applied or stale hash retained | Low | High | Dedicated `plan_changed` branch replaces plan/hash and returns to preview; test apply call count stays unchanged |
| Server path/category text becomes markup injection | Low | High | Build all rows with `el`/`textContent`; retain global `.innerHTML` prohibition and safe embedded JSON tests |
| CSP blocks same-origin fetch or gets widened too far | Medium | High | Add exactly `connect-src 'self'`; assert every directive and existing CDN restriction |
| Resync Cancel leaves visible data stale despite completed refresh | Medium | Medium | Cancel/zero-work Close reloads; no-store response headers prevent stale cache |
| Large organize preview freezes rendering | Medium | Medium | Category groups, document fragment/batched append, no duplicate transforms; measured smoke at current library scale |
| Poll timers multiply after retry/navigation | Medium | Medium | Single stored timer/job ID; clear before every start and on terminal/unload; structural markers plus HTTP job test |
| Modal Escape breaks existing search/detail Escape behavior | Medium | Medium | Modal gets first refusal only while open; existing branches remain unchanged and targeted keyboard smoke covers both |
| Generated output edited directly and later lost | Low | High | Source-only template edit; mandatory emit regeneration and structural assertion against `HTML_TEMPLATE` |

## Rollback Plan

Revert the `HTML_TEMPLATE` and viewer assertions, then run `python3 bin/index_assets.py emit` to restore generated `index.html`. Phase 1 and Phase 2 CLIs/APIs remain usable without viewer controls. No persisted viewer state or schema migration needs reversal; existing localStorage keys are untouched.
