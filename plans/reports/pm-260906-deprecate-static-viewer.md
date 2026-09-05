# Decommission Static Browser Viewer

Status: completed
Plan: `plans/260906-deprecate-static-viewer/plan.md`

| Area | Result |
| --- | --- |
| Deprecated artifacts | Removed root `index.html`, `Open Viewer.command`, and launcher test |
| Generator | Removed legacy HTML template/emitter; preserved CSV, state, review queue, and pending annotation |
| Server | API-only; removed static routes and `--open` browser launch flag |
| Tests | Updated route/output contracts; removed string-based legacy viewer tests |
| Verification | 253 Python tests, typecheck, build, API smoke, and `make dev` passed |

No routed documentation authority required updates. Historical plans and agent memory retain their original historical references.

Out of scope: pre-existing state-lock reacquisition in cleanup/organize subprocess paths.
