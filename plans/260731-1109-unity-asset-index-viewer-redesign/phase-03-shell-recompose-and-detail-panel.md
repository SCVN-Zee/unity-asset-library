---
phase: 3
title: "Shell Recompose and Detail Panel"
status: completed
priority: P1
effort: "4h"
dependencies: [2]
---

# Phase 3: Shell Recompose and Detail Panel

## Overview

Largest phase. Add a third zone to the shell, slim the card from 8+ text rows and 4 buttons
down to 4 elements and 1 focusable, and move the full record into a detail panel.

## Requirements

**Functional**
- Three-zone shell: rail 220px, results flexible, detail 320px shown on selection
- Card renders exactly: thumbnail, name (2-line clamp), author, total size
- Whole card is one focusable control that opens the detail panel
- Detail panel carries everything removed from the card, plus all four actions
- Table view keeps its existing columns

**Non-functional**
- Focusable elements in the result region drop from ~3,104 to ~835
- Detail panel is a column, not a modal. No focus trap
- Below 1100px the panel overlays; below 760px the rail collapses to a disclosure
- `Esc` closes the panel

## Architecture

### Shell

```
┌─────────────────────────────────────────────────────────────┐
│ Unity Asset Index  [search]  [sort v] [grid|list]  835 · 315 GB │ 56px, one line
├──────────┬──────────────────────────────────┬───────────────┤
│ rail     │ results                          │ detail        │
│ 220px    │ grid or table                    │ 320px         │
│          │                                  │ (on select)   │
│ Category │                                  │               │
│ Tags     │                                  │               │
│ Author   │                                  │               │
│ Flags    │                                  │               │
└──────────┴──────────────────────────────────┴───────────────┘
```

A column rather than a modal because modals need focus trapping and cost you your scroll
position. This is a browse tool; scanning continues while the panel is open.

### Card, before and after

| | Now | After |
|---|---|---|
| Content | name, disk name, author, breadcrumb, version/size/files, rating, 5 tags, N badges, 4 buttons | thumbnail, name, author, size |
| Focusable | 4 | 1 |
| Approx nodes each | ~22 | ~6 |

Long Unity names get `text-wrap:balance` plus a 2-line clamp, full string in `title`. Both
native CSS. Example needing it: `100+ Stylized Historical Textures - Medieval, Egyptian,
Roman & More`.

### Detail panel contents

Store name, on-disk name (prefixed `on disk: `), author, full breadcrumb, all tags,
rating / reviews / price, duplicate group membership as a count, integrity flags, every
version with its own file path / version / size / flags, then the four actions: `open`,
`copy path`, `store page`, `copy store URL`.

### Verbatim copy that must survive

Four existing tests match card and table strings that move into the panel. **Keep the copy
character-for-character** and those tests pass unchanged, which is cheaper than amending them.

| String | Guarded by |
|---|---|
| `copy store URL` | `test_store_url_has_a_slot_in_both_views` (526) |
| `store link pending` | same |
| `category pending` | same |
| `Store URL` | same, table column header, stays in the table |
| `on disk: ` | `test_viewer_shows_store_name_and_on_disk_name` (552) |
| `Name (store)`, `Name (on disk)` | same, table headers, stay in the table |
| `a.local_name` | same |

### Selection state

Module-scope `selected` holding an `asset_key`. `render()` re-applies the selected class; the
panel re-renders from `selected`. Keeps Phase 4's chunked append simple, since selection does
not live in the DOM.

### Badge cleanup

Phase 1 removed `.dup`, `.ns`, `.pend` CSS. Remove their JS call sites here. Cards show at most
the neutral chip and the warn chip. Duplicate membership moves to the panel as a count.

## Related Code Files

- Modify: `.index/bin/index_assets.py` - `<body>` markup, `card()`, new `detail()`, layout CSS,
  `render()` in `HTML_TEMPLATE`
- Modify: `.index/bin/tests/test_index_assets.py` - add `TestCardAndDetailSplit`

## Tests First

Add `TestCardAndDetailSplit`:

1. `test_card_has_one_focusable` - the `card()` body contains no `<button>`/`<a>` creation;
   the card element itself gets a `tabindex` or is a `button`
2. `test_actions_live_in_the_detail_panel` - `copy path`, `copy store URL`, `store page` appear
   inside the `detail(` function body, not `card(`. Split the template on function boundaries
   the way `test_facet_counts_deduplicate_flags` (486) already does
3. `test_detail_panel_element_exists` - `id="detail"` present in markup
4. `test_card_name_is_clamped` - `text-wrap:balance` and a line-clamp rule present
5. `test_escape_closes_the_panel` - a `keydown` listener referencing `Escape` exists
6. `test_verbatim_strings_survive_the_move` - assert all seven strings in the table above are
   still in the template

### Regression guards

- `test_store_url_has_a_slot_in_both_views` (526) and
  `test_viewer_shows_store_name_and_on_disk_name` (552) must pass **unamended**. If either
  fails, the copy was paraphrased. Restore the exact string rather than editing the test
- `test_viewer_renders_all_levels_not_just_level_one` (518) needs `categoryTree`, `levels`,
  `inCategory`, `indexOf(prefix+"/")` intact. The breadcrumb moves to the panel but must keep
  rendering every level
- `TestStalenessStamp` (928) needs `id="generated"` and the `stale` class in the header
- `test_rendering_uses_dom_apis_not_string_html` (391): no `.innerHTML` anywhere, including
  comments

## Implementation Steps

1. Write the six new tests. Confirm they fail
2. Add the `<aside id="detail">` zone to `<body>` and the three-column layout CSS with both
   breakpoints
3. Rewrite `card()` down to the four elements. Make the card element focusable and give it a
   click and `Enter` handler setting `selected`
4. Write `detail(a)` rendering the full record, moving the verbatim strings across intact
5. Delete the four per-card action buttons and the `.dup`/`.ns`/`.pend` badge call sites
6. Wire `Escape` to clear `selected` and hide the panel
7. Add the mobile disclosure for the rail below 760px
8. Run the full suite
9. Regenerate `index.html`

## Success Criteria

- [ ] Six new tests pass
- [ ] The two verbatim-copy tests pass **without amendment**
- [ ] Full suite green
- [ ] Clicking a card opens the panel with every field the card used to carry
- [ ] All four actions work from the panel, including clipboard copy
- [ ] `Esc` closes the panel
- [ ] Tab through a filtered grid reaches one stop per card
- [ ] Resize through 1100px and 760px: panel overlays, then rail collapses. No horizontal scroll
- [ ] Both themes still correct

## Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Paraphrasing a guarded string breaks two tests | Medium | The verbatim table is the checklist. Fix the copy, never the test |
| Panel and chunked render (Phase 4) fight over selection | Medium | Selection is module-scope keyed by `asset_key`, never a DOM reference |
| Clipboard actions silently fail from `file://` | Medium | Existing code already guards `navigator.clipboard &&`. Keep the guard and the "copied" confirmation |
| Card becomes a click target that swallows text selection | Low | Use a real `button` wrapper with `text-align:left`, and let the panel be the place text gets selected |
| Three columns overflow at 1100-1280px | Low | Rail 220 + panel 320 + min result width 480 = 1020, fits with gaps. Verify at 1100 exactly |
