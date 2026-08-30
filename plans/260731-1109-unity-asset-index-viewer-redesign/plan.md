---
title: "Unity Asset Index Viewer Redesign"
description: "Recompose the generated viewer: cool-neutral design system, remote-only thumbnails, three-zone shell with detail panel, and fixes for eager image load, per-keystroke re-render, and unfiltered facet counts."
status: completed
priority: P1
effort: "1.5d"
tags: [unity, asset-management, frontend, redesign, tdd]
created: 2026-07-31
mode: tdd
follows: 260730-1746-index-change-tracking
brainstorm: ../reports/brainstorm-260731-1104-index-viewer-redesign.md
---

# Unity Asset Index Viewer Redesign

## Overview

Redesign the front end of the generated asset viewer. Approved design:
[brainstorm-260731-1104-index-viewer-redesign.md](../reports/brainstorm-260731-1104-index-viewer-redesign.md).

**All work lands in `HTML_TEMPLATE`, `.index/bin/index_assets.py:679-978`** (299 lines: 51 CSS,
222 JS). `index.html` is generated output. Editing it directly is wiped by the next
`scan`/`update`.

## The single most important constraint

The test suite asserts on the **HTML source string**, not on runtime behavior. There is no
headless browser in the stack, and adding one (Playwright) would contradict the project's
zero-dependency offline ethos. So:

- **TDD coverage here is structural.** Tests can assert `loading="lazy"` appears in the
  template. They cannot assert an image actually deferred.
- **Behavioral verification is the manual checklist** in each phase's Success Criteria.
- Do not fake behavioral coverage with string assertions dressed up as behavior tests.

## Existing tests this redesign collides with

Read before touching anything. These are string matches against `ia.HTML_TEMPLATE`; a
redesign breaks several structurally even when behavior improves.

| Test | Line | Asserts | Handling |
|---|---|---|---|
| `test_indent_rule_exists_for_every_depth...` | 510 | `.facet.lvl1{` … `.facet.lvl4{` (whitespace-stripped) | **Preserve selectors verbatim** in the CSS rewrite |
| `test_viewer_renders_all_levels_not_just_level_one` | 518 | `categoryTree`, `levels`, `inCategory`, `indexOf(prefix+"/")` | **Preserve those identifiers and that expression verbatim** |
| `test_store_url_has_a_slot_in_both_views` | 526 | `"Store URL"`, `"copy store URL"`, `"store link pending"`, `"category pending"` | Strings move to the detail panel. **Keep the copy verbatim** and the test passes unchanged |
| `test_viewer_shows_store_name_and_on_disk_name` | 552 | `"Name (store)"`, `"Name (on disk)"`, `"on disk: "`, `a.local_name` | Same: keep copy verbatim in the detail panel |
| `test_facet_counts_deduplicate_flags` | 486 | splits template on the literal `function facetCounts()` then requires `indexOf(x)===i` | **Trap.** Changing the signature to `facetCounts(list)` makes the split fail with IndexError. Keep name and zero arity; read the filtered list from closure scope |
| `TestStalenessStamp` | 928-935 | `id="generated"`, `DATA.generated`, `"stale"` | Preserve the id and the class name |
| `test_rendering_uses_dom_apis_not_string_html` | 391 | `textContent` present, `.innerHTML` absent | Absent means **absent anywhere**, including in a comment |
| `TestHtmlEscaping._injects` | 318 | counts `img`/`svg`/`iframe` start tags **outside** `<script>`; currently 0 | **Images must stay `document.createElement("img")`.** A literal `<img>` in static markup breaks this |
| `test_no_external_resource_loading` | 384 | `src="http` absent, `@import` absent, CSP meta present | Passes literally (`src` set via `setAttribute`) but its **intent** is violated by remote thumbnails. Amend deliberately in Phase 2 |
| `TestBuildDeterminism` | 414 | two `build()` calls agree after popping `generated` | Do not thread new state into `build()` |

## Goals

| # | Goal | Priority |
|---|------|----------|
| 1 | Cool-neutral design system, one accent, one radius scale, both themes WCAG AA | P1 |
| 2 | Thumbnails served from CDN only; local `.index/thumbs` no longer read | P1 |
| 3 | Imageless card is a designed first-class state, not a fallback | P1 |
| 4 | Card carries 4 elements and 1 focusable; the rest moves to a detail panel | P1 |
| 5 | Eager 303 MB image load and per-keystroke full re-render both eliminated | P1 |
| 6 | Facet counts reflect the active filter | P2 |
| 7 | Rating sort added, Date sorts nulls last, table headers bound, keyboard basics | P2 |
| 8 | Generator stops mirroring; the 303 MB local cache is decommissioned | P2 |

## Phases

| # | Phase | Status |
|---|-------|--------|
| 1 | [Design System Foundation](./phase-01-design-system-foundation.md) | Completed |
| 2 | [Thumbnails Remote-Only](./phase-02-thumbnails-remote-only.md) | Completed |
| 3 | [Shell Recompose and Detail Panel](./phase-03-shell-recompose-and-detail-panel.md) | Completed |
| 4 | [Render Performance](./phase-04-render-performance.md) | Completed |
| 5 | [Facets, Sort, Keyboard](./phase-05-facets-sort-keyboard.md) | Completed |
| 6 | [Decommission Thumbnail Cache](./phase-06-decommission-thumbnail-cache.md) | Completed |

Dependencies: 1 → 2 → 3 → {4, 5} → 6. Phase 6 blocks on 2, 3, 4 **and** 5, because it deletes
the only local fallback and must not run until everything else is verified.

## Out of scope (deliberate)

- Virtualized rendering. Chunked append in Phase 4 is sufficient at 835 items
- New generator fields. Phase 6 *removes* one (`thumbnail.local`) but adds none
- URL-hash filter state. Reload still loses selections (open question 1)
- Web fonts. Impossible under `default-src 'none'`, not worth widening the CSP
- Downscaled thumbnails. The CDN does not resize and local mirroring is being removed
- Deleting `mirror_thumbnail` itself. Retained as the reversal path, with its ~8 security tests

## Success Criteria

- [ ] First paint renders without waiting on any network image
- [ ] Search stays interactive at 835 items
- [ ] Focusable elements in the result region drop from ~3,104 to ~835
- [ ] Facet counts change when a filter is applied
- [ ] Rating sort works; Date sorts nulls last; table headers respond
- [ ] Airplane mode: every card shows its imageless state, no layout shift
- [ ] Both color schemes pass WCAG AA on body text
- [ ] Zero em-dash characters in any shipped UI string (8 ship today, listed in Phase 1)
- [ ] Generator writes no new local mirrors; `.index/thumbs/` decommissioned
- [ ] Full suite green, with only the Phase 2 amendment to `test_no_external_resource_loading`

## Open questions

1. **URL-hash filter state?** Scoped out. Worth a follow-up plan if reload-loses-filters annoys.
2. **Stale phase metadata in the two predecessor plans.** All four `phase-*.md` files say
   `status: todo` while both parent `plan.md` files say `completed`, contradicted by the
   shipped code. Cosmetic, unowned by this plan.
3. **Author facet caps at 25 of 284 authors**, silently. Pre-existing, not introduced by
   this plan; 91% of authors are unreachable as a filter. Raise, add a "+N more", accept?
4. **`--line` borders are 1.23:1 / 1.31:1 against `--bg`.** WCAG 1.4.11 wants 3:1 for
   control boundaries. Body text passes AA in both schemes, which is what this plan's
   criterion asked for. Deliberate aesthetic, or raise?
5. **`overrides.json` is applied unvalidated**, so a `javascript:` store URL reaches an
   `<a href>`. Self-XSS on a user-authored local file, not a trust-boundary crossing.
   Validate, or treat as trusted input?
6. **Commit a jsdom smoke test?** The 39 new string tests were 0-for-4 on the JS defects
   the review found. A headless browser was scoped out as contradicting the offline
   zero-dependency ethos -- true of the shipped artifact, arguable for the test suite.

## Validation Log

### Session 1 - 2026-07-31

**Verification Results**
- Claims checked: 27
- Verified: 25 | Failed: 2 | Unverified: 0
- Tier: Full (5+ phases at time of run)
- Failures, both mine, both corrected in place:
  - "127 assets without a thumbnail" was wrong. Actual **118** (835 total minus 717 with a
    thumbnail). Corrected in `phase-02` and the brainstorm report
  - "~3,340 focusable elements" was an upper bound assuming every card has 4 actions. Actual
    **3,104**: `open` + `copy path` on all 835, plus `store page` + `copy store URL` on the 717
    with a store URL. Corrected in `plan.md`, `phase-03`, and the brainstorm report
- Incidental finding, not a plan error: **the current template already ships 8 em-dashes**
  across 6 UI strings (`"- author pending -"`, `"- category pending -"`, and 5 table
  placeholders). Phase 1's new test fails against them on day one. The replacement strings are
  safe against `test_store_url_has_a_slot_in_both_views`, which matches the substring
  `category pending`. Documented in Phase 1 with exact template line numbers
- Confirmed by reading source, not assumed: CSS block spans lines 686-737; `table()` binds no
  listeners and there is no delegated `th` handler, so the pointer cursor is genuinely a lie;
  the `input` listener at line 969 is bare `render` with no debounce; `facetCounts()` iterates
  `assets` globally; sort options are exactly name/size/date; suite is 179 tests

**Decisions**

| Question | Decision | Consequence |
|---|---|---|
| Stop mirroring in `resolve_store.py`? | **Yes** | Added to Phase 2. Remove the three `mirror_thumbnail` call sites (582-588, 608-614, 1035). **Keep the function** and its ~8 tests: it is the reversal path and carries host-allowlist plus scheme-rejection coverage. Phase 2 effort 1.5h to 2h |
| Delete `.index/thumbs/` (303 MB, 708 files)? | **Yes, as part of this work** | New **Phase 6**, gated on Phases 2-5 all verified. Deletion is the only irreversible step here and recovery costs 708 rate-limited fetches, so it runs last and requires explicit confirmation at the moment of deletion |
| Local fallback when a CDN image fails? | **No** | Remote or placeholder, nothing between. Keeps `img-src 'self'` out of the CSP and keeps the airplane-mode check unambiguous. New Phase 2 test `test_no_local_fallback_path` |
| Phase 5 (P2) in this round? | **Include** | Closes audit findings 3, 6, 7. Phase 6 now depends on 5 as well |

**Propagation**
- `phase-02`: mirroring stop, 2 new tests (7 total), 3 new success criteria, revised risk table
- `phase-06`: created
- `plan.md`: goal 8 added, phases table and dependency chain updated, out-of-scope revised,
  success criteria revised, open questions 1-2 resolved and removed

### Session 2 - 2026-07-31 (implementation + review)

All 6 phases implemented TDD. Suite 179 -> 222 tests, green. `.index/thumbs/` deleted
(708 files, 302,601,099 B) after explicit user confirmation.

**The plan's central assumption was wrong, and it matters.** "TDD coverage here is
structural" and "adding a headless browser would contradict the zero-dependency ethos"
were correct about the *artifact* and wrong about the *test suite*. A jsdom harness run
during review found **4 defects that all 39 new string tests passed over**, including a
critical one that silently dropped 98 of 835 results. Full write-up:
[code-review-260731-1125-index-viewer-redesign.md](../reports/code-review-260731-1125-index-viewer-redesign.md).

Fixed in this session: stale-`IntersectionObserver` race (C1), list-view unreachable by
mouse (H1), selection/cursor desync (H2), rail unreachable after resize (M1), 5x haystack
rebuild (M2), panel focus contract (M3), facet focus loss (M4), missing ARIA (M6),
unencoded href (L2), and four test-quality defects including a brace-matched replacement
for the fragile `_fn_body` slicer.

Verification now runs at three layers: 222 unit tests, 52 behavioural jsdom checks, 27
targeted regression checks. The C1 guard was proved non-vacuous by reverting it alone
(735 of 835 render without it).

### Whole-Plan Consistency Sweep

Run after propagation. Reconciled:
- "Phases 1-5 assume mirroring is untouched" removed from open questions; contradicted by the
  Phase 2 decision
- Phase 2's risk row citing "until open question 2 is settled" rewritten to point at Phase 6
- Out-of-scope line "Data-model changes, new generator fields, changes to `build()` output"
  contradicted Phase 6, which removes `thumbnail.local` from `build()`. Narrowed to "new
  generator fields"
- Open questions renumbered after two were resolved; the `plan.md` out-of-scope reference to
  "open question 3" repointed to open question 1
- Focusable-count and thumbnail-count corrections applied in all four locations

**Unresolved contradictions: none.**

<!-- slug: unity-asset-index-viewer-redesign -->
