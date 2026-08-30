---
phase: 4
title: "Render Performance"
status: completed
priority: P1
effort: "1.5h"
dependencies: [3]
---

# Phase 4: Render Performance

## Overview

Stop rebuilding the whole result set on every keystroke. Debounce the search input and append
cards in chunks so first paint is immediate.

## Requirements

**Functional**
- Search input debounced at 120ms
- Results append in chunks of 100 via an IntersectionObserver sentinel
- Sort, view toggle, and facet clicks re-render immediately, no debounce
- Chunking resets cleanly on every filter change; no duplicated or orphaned cards

**Non-functional**
- No `window.addEventListener("scroll")`
- Observer disconnected and rebuilt on each re-render, no leak across renders

## Architecture

### What is wrong today

`render()` runs on every `input` event with no debounce. At 835 items it rebuilds the full
result set and then calls `renderFacets()`, which recomputes every facet count over the whole
library. Phase 3 cut per-card nodes from ~22 to ~6, so the remaining cost is dominated by
sheer count plus the facet recount.

### Debounce

Wrap only the `input` handler. Sort `change`, view `click`, and facet `click` stay synchronous
because they are discrete actions where latency reads as lag.

```
var t; q.addEventListener("input", function(){
  clearTimeout(t); t = setTimeout(render, 120);
});
```

### Chunked append

```
CHUNK = 100
render() computes the filtered+sorted list, resets the output node,
        appends the first chunk, then observes a sentinel at the end.
sentinel intersects -> append the next chunk -> re-observe or disconnect when drained.
```

Grid view only. Table view builds rows with far fewer nodes each and stays whole; splitting it
would complicate `<tbody>` handling for no measurable gain.

**Not virtualization.** Nodes already appended stay in the DOM. That was scoped out
deliberately: at 835 items the ceiling is ~5,000 nodes, and virtualization would add scroll
math, height estimation, and a class of bugs disproportionate to the benefit.

### Interaction with lazy images

Phase 2 set `loading="lazy"`. Chunked append and lazy loading compose: a chunk enters the DOM,
and only the images actually inside the viewport are fetched. Do not add a second manual
image-loading mechanism.

### Ordering constraint

Facet counts (Phase 5) are computed **once per render from the filtered list**, before
chunking begins. Chunk appends must never trigger a recount.

## Related Code Files

- Modify: `.index/bin/index_assets.py` - `render()` and the input listener in `HTML_TEMPLATE`
- Modify: `.index/bin/tests/test_index_assets.py` - add `TestRenderPerformance`

## Tests First

Add `TestRenderPerformance`:

1. `test_search_input_is_debounced` - the `input` listener body references `setTimeout` and
   `clearTimeout`
2. `test_no_scroll_event_listener` - the template contains no
   `addEventListener("scroll"` (allow whitespace variants)
3. `test_chunked_append_uses_intersection_observer` - `IntersectionObserver` present, and a
   chunk-size constant is defined
4. `test_observer_is_disconnected_between_renders` - `disconnect(` present
5. `test_sort_and_view_are_not_debounced` - the `change` and view `click` handlers call
   `render` directly, not through the debounce wrapper

**Honest limit:** these confirm the mechanism is wired, not that it is fast. Throughput is
verified manually below.

## Implementation Steps

1. Write the five tests. Confirm 1, 3, 4 fail
2. Extract the filter-and-sort step of `render()` from the append step
3. Add the debounce wrapper to the `input` listener only
4. Add the chunk constant, the sentinel node, and the observer. Disconnect any prior observer
   at the top of every render
5. Confirm facet counting happens once, before the first chunk
6. Run the full suite
7. Regenerate `index.html`

## Success Criteria

- [ ] Five tests pass
- [ ] Full suite green
- [ ] Typing a multi-character query stays responsive with no visible stutter
- [ ] Scrolling to the bottom of the unfiltered 835-item grid loads every card, exactly once
- [ ] Clearing the search returns to the full set with no duplicate cards
- [ ] Rapid filter toggling leaves no orphaned sentinel and no stale observer
- [ ] DevTools Performance: no long task over ~200ms during a keystroke

## Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Sentinel fires repeatedly and appends duplicates | Medium | Disconnect before re-observing; track the append cursor in module scope, reset it at the top of `render()` |
| Debounce swallows the final keystroke | Medium | 120ms is below the point where it reads as lag; the timer is cleared and reset per event, never skipped |
| Observer leaks across renders | Medium | Test 4 asserts `disconnect(`. Verify in DevTools that observer count does not climb while filtering |
| Chunking hides results the user believes are absent | Low | The header count always reports the full filtered total, independent of how many chunks have rendered |
| Table view left unchunked feels inconsistent | Low | Accepted. Rows are cheap; revisit only if measurement says otherwise |
