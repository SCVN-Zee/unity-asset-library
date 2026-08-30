---
phase: 5
title: "Facets, Sort, Keyboard"
status: completed
priority: P2
effort: "2.5h"
dependencies: [3]
---

# Phase 5: Facets, Sort, Keyboard

## Overview

Behavior fixes independent of the visual work: facet counts that respect the active filter,
a Rating sort, a Date sort that stops lying, bound table headers, and keyboard basics.

P2. Deferrable without breaking Phases 1-4.

## Requirements

**Functional**
- Facet counts computed from the filtered result set
- A facet family's own selection is excluded from its own counts (standard faceted search)
- Rating sort added
- Date sort places null dates last instead of collapsing them to empty string
- Table headers sort on click
- `/` focuses search, `Esc` clears search or closes the panel, `Up`/`Down` and `Enter` work in table view

**Non-functional**
- `facetCounts` keeps its exact name and zero arity (see the trap below)
- Counts computed once per render, before Phase 4's chunking

## Architecture

### The `facetCounts` trap

`test_facet_counts_deduplicate_flags` (line 486) does:

```python
js = ia.HTML_TEMPLATE.split("function facetCounts()", 1)[1].split("function ", 1)[0]
self.assertIn("indexOf(x)===i", js)
```

It splits on the **literal** `function facetCounts()`. Changing the signature to
`facetCounts(list)` makes the split raise IndexError and the test errors rather than fails.

**Keep the name and zero arity.** Read the filtered list from a module-scope variable set by
`render()` before the call. Keep the `indexOf(x)===i` flag-dedup expression, which exists
because a duplicate group is one asset with two flagged versions and naive counting reported
20 duplicates where there were 10.

### Filtered counts, and why a family excludes itself

Today `facetCounts()` iterates `assets` and always reports library-wide totals, so selecting
`3D` leaves every tag count unchanged. Counts must come from the filtered set.

Standard faceted-search refinement: when counting facet family F, apply every filter **except
F's own selections**. Otherwise selecting one tag drops every other tag in that family to
zero and the user can never widen without first deselecting.

Category is a prefix tree and keeps its existing `inCategory` prefix semantics, counted the
same way: category counts apply the non-category filters only.

### Sort

| Key | Behavior |
|---|---|
| `name` | unchanged, default |
| `size` | unchanged, descending total bytes |
| `rating` | new, descending; 283 unrated assets sort last |
| `date` | keep, descending, **nulls last** |

Date is currently populated on 214 of 861 versions (25%). The existing comparator collapses
nulls to `""`, which sorts them into one indistinguishable block at an arbitrary end. Explicit
null handling makes the 75% coverage gap legible rather than silently wrong. Demote `date`
below `rating` in the select order.

### Table headers

`th` is already styled `cursor:pointer` with no handler bound, which is a lie the UI tells on
every hover. Bind each to the matching sort key. Columns with no meaningful order (Package
path) stay unbound and lose the pointer cursor.

### Keyboard

| Key | Action |
|---|---|
| `/` | focus search, unless already in an input |
| `Esc` | clear a non-empty search, else close the detail panel |
| `Up`/`Down` | move selection in table view |
| `Enter` | open the detail panel for the selected row |

Grid view gets `/` and `Esc` only. Two-dimensional arrow navigation needs live column-count
math against a responsive `auto-fill` grid; cost outweighs value here. Tab already traverses
the grid one stop per card after Phase 3.

## Related Code Files

- Modify: `.index/bin/index_assets.py` - `facetCounts`, `renderFacets`, `render` sort block,
  `table()` headers, new keyboard listener in `HTML_TEMPLATE`
- Modify: `.index/bin/tests/test_index_assets.py` - add `TestFacetRefinement`,
  `TestSortOptions`, `TestKeyboard`

## Tests First

`TestFacetRefinement`
1. `test_facet_counts_signature_is_unchanged` - the literal `function facetCounts()` is
   present, guarding the split in test 486
2. `test_counts_read_the_filtered_list` - `facetCounts` body references the module-scope
   filtered variable, not `assets` directly
3. `test_family_excludes_own_selection` - the refinement helper is present and takes the
   family being counted as an argument

`TestSortOptions`
4. `test_rating_sort_exists` - a `rating` option in the select and a `rating` branch in the
   comparator
5. `test_date_sort_puts_nulls_last` - the comparator has an explicit null branch and no longer
   relies on `||""`
6. `test_table_headers_are_bound` - `table()` attaches a click listener to sortable headers

`TestKeyboard`
7. `test_slash_focuses_search` and `test_escape_is_bound` - a `keydown` listener referencing
   both keys

### Regression guards

- `test_facet_counts_deduplicate_flags` (486) must pass **unamended**. If it errors with
  IndexError, the signature was changed. Restore it
- `test_viewer_renders_all_levels_not_just_level_one` (518) needs `categoryTree`, `levels`,
  `inCategory`, `indexOf(prefix+"/")` intact
- Phase 4's single-count-per-render ordering must survive: counts before chunking

## Implementation Steps

1. Write the seven tests. Confirm the new ones fail and 486 still passes
2. Add the module-scope filtered list, set in `render()` before `renderFacets()`
3. Rewrite `facetCounts()` (name and arity unchanged) to read it
4. Add the per-family refinement so each family excludes its own selections
5. Add the `rating` comparator branch and the select option; reorder the select
6. Rewrite the `date` branch with explicit null-last handling
7. Bind sortable table headers; drop the pointer cursor from unsortable ones
8. Add the keyboard listener
9. Run the full suite
10. Regenerate `index.html`

## Success Criteria

- [ ] Seven new tests pass
- [ ] `test_facet_counts_deduplicate_flags` passes **unamended**
- [ ] Full suite green
- [ ] Selecting `3D` visibly changes tag, author, and flag counts
- [ ] With one tag selected, the other tags in that family still show reachable non-zero counts
- [ ] Rating sort puts 5-star assets first and unrated assets last
- [ ] Date sort puts the 214 dated versions first and undated last, neither silently interleaved
- [ ] Clicking `Size` in table view sorts by size
- [ ] `/` focuses search from anywhere; `Esc` clears then closes
- [ ] Facet counts stay correct while Phase 4 chunks are still appending

## Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| **Changing `facetCounts` arity errors test 486 with IndexError** | High | Test 1 guards the literal signature. Use closure scope, never a parameter |
| Self-exclusion misapplied, counts read as wrong | Medium | Verify by hand: select one tag, confirm sibling tags still show reachable totals |
| Recount runs per chunk and tanks the Phase 4 win | Medium | Count once in `render()` before the first append. Success criterion covers it |
| `/` hijacks typing inside the search field | Medium | Ignore the key when `document.activeElement` is an input |
| Assuming `rating` needs coercion | Low | Verified: `rating` is a **float** (552 values, 1.0-5.0) and `reviews` an **int**. No `parseFloat` needed. `price` is the string field (687 values); coerce that one if it ever becomes sortable |
| Date sort still misleads at 25% coverage | Low | Nulls-last makes the gap visible. Populating dates is a generator concern, out of scope |
