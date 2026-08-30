# Code review: Unity asset index viewer redesign

Plan: [260731-1109-unity-asset-index-viewer-redesign](../260731-1109-unity-asset-index-viewer-redesign/plan.md)
Date: 2026-07-31 · Reviewer: `code-reviewer` subagent (jsdom harness, not source reading)

## Headline

Python and the data migration were clean. **The JS was not.** A ~150-line jsdom harness
found 4 defects that all 39 new string tests passed over, one of which silently dropped
98 of 835 results. Every finding below is fixed and re-verified except the four left as
open questions.

The string-assertion approach the plan chose was 0-for-4 on real JS defects. That is the
single most important outcome of this review.

## Fixed

| ID | Severity | Defect | Fix |
|---|---|---|---|
| C1 | Critical | `IntersectionObserver.disconnect()` does not drop already-queued entries. A superseded render's observer still fired once, appending the *new* list into the *old detached* grid, advancing the shared `cursor` past those items, then calling `disconnect()` on whichever observer module-scope `obs` now pointed at (the live one) and `removeChild` on a detached sentinel (throws). Measured pre-fix: **735 of 835 rendered, 98 missing, 10 duplicated**; worst form froze the grid at 100 permanently | Generation token: `var mine=++gen` per render, `if(mine!==gen)return;` as the callback's first statement |
| H1 | High | `table()` bound click only on the two sortable `th`. Since Phase 3 moved rating, tags, flags, duplicate groups, version list, `open`, `copy path`, `store page`, `copy store URL` into `#detail`, a **mouse user in list view could reach none of it** | Row click opens the panel; clicks on a row's `<a>` pass through |
| H2 | High | Selection desynced three ways: `table()` never read `selected` (grid→list lost the mark); `closePanel()` cleared only `.card.pick`, leaving `tr.pick` behind; arrow cursor and panel selection shared one class and were visually indistinguishable | Single painter `paintRows()` driven by `(rowIdx, selected)`; distinct `.cur` (cursor) vs `.pick` (selected); `closePanel()` repaints |
| M1 | Medium | `matchMedia` was sampled once at load. Resizing mobile→desktop left the rail collapsed with `summary{display:none}` — **no filter UI and no affordance to restore it** | `syncRail()` bound to a `change` listener on the media query |
| M2 | Medium | `facetPool` added 4 extra passes over all 835 assets per render; the search haystack was rebuilt and lowercased **5× per render** (4,175 `matches()` calls). A real 5× regression introduced by Phase 5 | Haystack memoized once per asset (`a._hay`) |
| M3 | Medium | `#out` precedes `#detail` in DOM order, so the panel was ~835 tab stops from the card that opened it; closing dropped focus to `<body>` | Open focuses the panel; close restores focus to the originating card/row |
| M4 | Medium | `renderFacets()` destroys the clicked button, dropping focus to `<body>`. A keyboard user could not pick two facets without re-tabbing from the document start | Focused facet remembered by label and re-focused after rebuild |
| M6 | Medium | Zero ARIA. `th.sortable` was a focusable control with no `role`/`aria-sort`; `#count` changed silently; card's accessible name was its children run together (`"Audio(SE) Bark Howl Growlauthor pending7.7 MB"`) | `role="button"` + `aria-sort` on sortable headers, `aria-live="polite"` on `#count`, `aria-label` on cards, `aria-label` on the panel |
| L2 | Low | `"./"+v.file` unencoded; a `#` or `?` truncates the href. Latent (0 of 861 filenames affected) | `encodeURI()` |
| T1 | Test | `_fn_body("render()")` returned 99 lines to end-of-file, so a scoped-looking assertion covered the whole tail | Brace-matched slicer |
| T2 | Test | `_fn_body("card(a)")` swallowed the next function's comment block — **the exact bug class already hit once during implementation** | Same fix; verified slices end on the right `}` |
| T3 | Test | Four tests named for behaviour asserted only that a string appeared somewhere. `test_observer_is_disconnected_between_renders` passed on C1 | Strengthened to slice the handler and assert the call; 5 new tests added |
| T4 | Test | Docstring claimed the live cache still carries `thumbnail_local`; Phase 6 had stripped all 717 | Corrected |

## Verified clean

- **XSS**: hostile data (`</script><img onerror>`, `<svg onload>`, `javascript:` thumbnail,
  `<iframe>` author) driven through the real generator and booted — 0 smuggled tags,
  hostile input rendered as inert text. Every data path goes through `textContent` /
  `setAttribute`. `.innerHTML` appears exactly once: inside the assertion forbidding it.
- **`resolve_store.py`**: no regressions. All call sites of `attach_detail` and `merge`
  are arity-correct. `mirror_thumbnail` reachable only from its own tests, exactly as
  intended (reversal path retained).
- **Data migration** (diffed against pre-change backups): `assets.json` — 717 entries
  `{local,remote}`→`{remote}`, 118 `None` untouched, **zero diffs outside `thumbnail`**.
  `cache.json` — exactly the one key removed from 717 of 825 records, no other deltas.
- **All 8 fragile pre-existing tests** pass with their invariants intact.
- **Acceptance criteria**: all 7 measured and passing. Contrast confirmed —
  light `--ink-3`/`--sunk` **4.80:1**, dark `--ink-3`/`--surface` **4.66:1**.

## Verification

| Layer | Result |
|---|---|
| `python3 -m unittest discover` | **222 tests, OK** (179 at plan start) |
| Behavioural harness (jsdom, real `index.html`, 835 assets) | **52 checks, 0 failed** |
| Regression harness (targets each finding above) | **27 checks, 0 failed** |

The C1 test was proved non-vacuous: reverting only `if(mine!==gen)return;` in a throwaway
copy reproduces **735 of 835**, matching the review's independent measurement.

Harnesses live in the session scratchpad and are **not** committed — jsdom is a scratch
dependency, never part of the project's stdlib-only offline suite. See open question 4.

## Open questions

1. **Author facet caps at 25 of 284 authors** with no "+259 more" and no indication it is
   capped (`renderFacets`, `keys.slice(0, 25)`). Pre-existing, not introduced here. 91% of
   authors are unreachable as a filter. Raise the cap, add an affordance, or accept?
2. **`--line` borders are 1.23:1 (light) / 1.31:1 (dark) against `--bg`.** WCAG 1.4.11
   wants 3:1 for control boundaries; the search input and buttons are delimited by that
   border alone. Outside the plan's stated criterion (body text only), which passes.
   Deliberate low-contrast aesthetic, or raise?
3. **`overrides.json` values are applied unvalidated** (`merge()`), so a `store` of
   `javascript:…` lands in an `<a href>` and `script-src 'unsafe-inline'` permits it.
   Provenance is a user-authored local file, so this is self-XSS on `file://`, not a
   trust-boundary crossing — the network path fails closed on host/scheme. Validate
   anyway, or treat the file as trusted input?
4. **Commit a jsdom smoke test?** The 39 string tests were 0-for-4 on the JS defects found
   here. The plan scoped out a headless browser as contradicting the zero-dependency
   offline ethos, and that reasoning still holds for the shipped artifact — but the test
   suite is not the artifact. One devDependency that never ships, versus the demonstrated
   blind spot.
