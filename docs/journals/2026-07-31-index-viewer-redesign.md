# Index Viewer Redesign — 39 New Tests, 0-for-4 on the Bugs That Mattered

**Date**: 2026-07-31
**Severity**: Medium
**Component**: `.index/bin/index_assets.py` (`HTML_TEMPLATE`), `.index/bin/resolve_store.py`
**Status**: Complete (6/6 phases; 4 open questions for the user)

## What Happened

Shipped all six phases of the viewer redesign TDD: design-token system, remote-only
thumbnails, three-zone shell with a detail panel, debounced + chunked rendering, filtered
facet counts with per-family self-exclusion, and decommissioning of the 302 MB local
thumbnail mirror. Suite went 179 → 222 tests, green throughout. `.index/thumbs/` (708
files, 302,601,099 B) deleted after explicit confirmation.

Then a code review ran a jsdom harness against the generated page and found four defects
the 39 new tests had all passed over.

## The Brutal Truth

The plan opened with a section titled "The single most important constraint," which
argued that because the suite asserts on the HTML source string and adding Playwright
would contradict the project's offline zero-dependency ethos, **TDD coverage here is
structural** and behavioural verification is a manual checklist. I accepted that framing
and wrote 39 tests inside it. All 39 passed on code that silently dropped 98 of 835
results.

The conflation was mine to catch and I didn't: the *artifact* must be zero-dependency and
offline. The *test suite* is not the artifact. A devDependency that never ships was never
in tension with the ethos, and the plan's own honest disclaimers — "these confirm the
mechanism is wired, not that it is fast" — were flagging the gap on every phase while I
kept writing to the same pattern.

Worse, I had node available and used it from Phase 3 onward, but only for `node --check`
syntax validation. The tool that eventually found the critical bug was sitting in my hand
for four phases while I used it to check for typos. I only reached for jsdom before the
irreversible deletion, and even then framed it as pre-deletion insurance rather than as
the thing the test strategy had been missing all along.

`test_observer_is_disconnected_between_renders` asserts `"disconnect(" in HTML_TEMPLATE`.
It passes on the broken code. I wrote it, and its name promised something its assertion
could not see.

## Technical Details

- **C1, critical**: `IntersectionObserver.disconnect()` unregisters targets but does *not*
  drop already-queued entries, so a superseded render's observer still gets one delivery.
  The callback closed over the per-render grid `g` and sentinel `sen` while reading
  module-scope `cursor` / `view` / `obs`. A stale delivery therefore appended the *new*
  list into the *old detached* grid, advanced the shared cursor past those items, then
  called `disconnect()` on whichever observer `obs` now pointed at — the live one — and
  `removeChild` on a detached node (throws). Measured: **735 of 835 rendered, 98 missing,
  10 duplicated**; the worst interleaving froze the grid at 100 results permanently. The
  120 ms debounce makes the race *more* likely, not less. Fixed with a generation token.
- **H1**: Phase 3 moved every action into the detail panel, and `table()` bound click only
  on the two sortable `th`. In list view a mouse user could reach none of it. Keyboard
  users could, which is why it survived my own checks.
- **H2**: the arrow-key cursor and the panel selection shared one `.pick` class and one
  set of in-place mutations. Split into `.cur` / `.pick` painted from `(rowIdx, selected)`.
- **M2**: my own Phase 5 facet refinement turned one filter pass into five (4,175
  `matches()` calls per render), each rebuilding and lowercasing the full search haystack.
  In a phase explicitly about render performance.
- **Test-slicing**: `_fn_body` split on the next `\nfunction `, so it swallowed
  neighbouring comments and ran to end-of-file for the last function. This bit me twice —
  once during implementation, when a comment I wrote containing the literal
  `function facetCounts()` broke the helper, and again at review. Replaced with a
  brace-matched slicer.

## What We Tried

- **String assertions as the primary gate** — the plan's design. Caught structure
  faithfully (CSP directives, verbatim copy, token coverage, radius scale) and caught
  nothing behavioural.
- **`node --check`** from Phase 3 — real value, zero cost, caught syntax only. Not a
  behavioural check, and I let it feel like one.
- **jsdom harness** — 52 behavioural checks against the real 885 KB page with real data,
  plus 27 written to target each review finding. Found the duplicate-name data property
  (8 names shared across assets, `asset_key` is the unique field) that made three of my
  own early assertions wrong, then found nothing else — because the review had already
  found the rest.
- **Proving the fix non-vacuous** — reverted only `if(mine!==gen)return;` in a throwaway
  copy and reproduced 735/835 exactly. Worth the two minutes: it is the difference between
  a test that passes and a test that would have failed.

## Root Cause Analysis

The plan's constraint section was *reasoning about the wrong artifact*, and it was
persuasive enough — written in the same voice as the correct constraints around it — that
I inherited its conclusion instead of testing it. Every later phase then compounded it:
each new test was written to match the established pattern, so the blind spot scaled
linearly with the work.

The tell was there from Phase 4 onward. I wrote "**Honest limit:** these confirm the
mechanism is wired, not that it is fast" and treated it as a disclosure rather than as a
defect report about my own coverage. Naming a gap is not the same as closing it, and
writing the disclaimer made me feel like I had.

The secondary cause is that I verified against the plan's success criteria rather than
against the system. Every criterion passed on the broken code, because none of them said
"filter rapidly while a chunk is in flight."

## Lessons

- **"Zero-dependency" is a property of what ships, not of how it is checked.** Ask which
  artifact a constraint actually binds before inheriting its conclusion.
- **A test whose name promises behaviour must fail when that behaviour breaks.** If the
  assertion cannot see the behaviour, rename the test or delete it — an honest docstring
  under a misleading name still reads as coverage in the summary line.
- **Reach for the strongest available tool at the point the risk appears**, not at the
  point something irreversible is about to happen. node was present from Phase 3.
- **When a plan hands you a limitation, treat it as a claim to verify**, at the same
  standard as the plan's factual claims. This one was validated for 27 claims and its
  central methodological assumption was not among them.

## Open Questions

1. Author facet silently caps at 25 of 284 authors (pre-existing) — raise, add "+N more",
   or accept?
2. `--line` borders at 1.23:1 / 1.31:1 vs WCAG 1.4.11's 3:1 for control boundaries. Body
   text passes AA, which is what the plan asked for. Deliberate aesthetic, or raise?
3. `overrides.json` applied unvalidated — a `javascript:` store URL reaches an `<a href>`.
   Self-XSS on a user-authored local file. Validate, or treat as trusted?
4. Commit a jsdom smoke test? The evidence for it is this journal.
