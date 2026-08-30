---
name: project-unity-asset-index-tool
description: Status of the .index/bin asset-indexing tool (Unity Asset Store library) - Phase 2 enrichment is complete, and the non-obvious review traps in its HTML_TEMPLATE viewer
metadata:
  type: project
---

`.index/bin/index_assets.py` (scan/parse/dedupe/emit viewer) and `.index/bin/resolve_store.py`
(store enrichment) implement `plans/260730-1154-unity-asset-index/`. Zero-dependency Python 3
stdlib + unittest only. `index.html` is GENERATED from `index_assets.py`'s `HTML_TEMPLATE`
string; never edit it directly.

**State as of 2026-07-31:** enrichment finished — 717 of 835 assets id-verified, 552 rated,
`.index/cache.json` holds 825 resolved records. The two Critical bugs I logged on 2026-07-30
(unguarded `fetch_detail` in `main()`; `legacy_lookup` missing `_looks_blocked`) are **both
fixed** — verified by reading `resolve_store.py` on 2026-07-31. Do not re-raise them.

**Why this matters for review:** the viewer's whole test surface is Python string-matching
against `HTML_TEMPLATE`. That cannot catch JS runtime bugs, and it has already produced two
distinct false results: (1) a comment containing a literal that a slicing helper split on,
(2) `test_observer_is_disconnected_between_renders` passing on code with a live
IntersectionObserver race. Treat "218 tests green" as evidence about the Python, not the JS.

**How to apply:** to review the viewer's JS, load the emitted `index.html` in jsdom and drive
it — do not read the diff alone. jsdom 29 + node 24 are already installed in this session's
scratchpad; a ~150-line harness (stub `IntersectionObserver` with a manual `fire()`, stub
`matchMedia`, stub `scrollIntoView`) exercises real behaviour against the real 835-asset
payload and found 4 bugs the 39 string tests missed. Also: `.index/review-queue.md`'s top
"Duplicates" entry (AllSky 5.2.0 vs 5.1.0, ~5.95 GB) is a false-ish positive from `group()`'s
`same-size-different-version` verdict — two legitimate releases sharing a byte size, not
redundant copies. See [[onedrive-dataless-archives]] for the read-only constraint on this tree.
