---
phase: 1
title: "Design System Foundation"
status: completed
priority: P1
effort: "2h"
dependencies: []
---

# Phase 1: Design System Foundation

## Overview

Replace the 51-line CSS block in `HTML_TEMPLATE` with a cool-neutral token system: one accent,
one radius scale, tabular numerals, density 7. Pure CSS. No JS, no markup change.

## Requirements

**Functional**
- Both color schemes defined via CSS custom properties, swapped by `prefers-color-scheme`
- Accent used only for active filter, focus ring, selected row
- Every numeric display carries `tabular-nums`

**Non-functional**
- No pure `#000000` or `#ffffff`
- One radius scale: 6px containers, 4px chips. No pills
- Body text WCAG AA in both schemes
- Zero em-dash characters in any CSS `content` string

## Architecture

### Tokens

```css
:root{
  --bg:#fbfbfc; --surface:#fff; --sunk:#f4f4f6;
  --ink:#18181b; --ink-2:#52525b; --ink-3:#a1a1aa;
  --line:#e4e4e7; --accent:#2563eb; --warn:#b45309;
  --r:6px; --r-sm:4px;
}
@media(prefers-color-scheme:dark){:root{
  --bg:#0c0c0e; --surface:#151518; --sunk:#0a0a0b;
  --ink:#fafafa; --ink-2:#a1a1aa; --ink-3:#71717a;
  --line:#27272a; --accent:#3b82f6; --warn:#d97706;
}}
```

`--surface:#fff` in light is the one near-pure value and is intentional: cards need to sit
above `--bg:#fbfbfc`. Dark mode has no pure values.

### Type

```
font-family: ui-sans-serif,-apple-system,"SF Pro Text",system-ui,sans-serif
scale: 11 / 12 / 13 / 15 / 20
font-variant-numeric: tabular-nums  on .count, .facet .n, card size, table numerics, rating
```

Resolves to SF Pro on macOS. Web fonts are impossible under `default-src 'none'`.

### Badge reduction

Five badge colors today (`accent` red, `#8a6d00` warn, `#3a5a8a` dup, `#5a5a5a` ns,
`#5a3a8a` pend) collapse to two:

| Class | Use | Color |
|---|---|---|
| `.badge` | informational: `non-store`, `variant`, `pending-enrichment` | neutral chip, `--ink-3` on `--sunk` |
| `.badge.warn` | integrity: `suspicious`, `broken` (7 assets total) | `--warn` |

`duplicate` stops being a card badge; it becomes a count in the detail panel (Phase 3).

## Related Code Files

- Modify: `.index/bin/index_assets.py` - CSS block inside `HTML_TEMPLATE` (lines ~686-737)
- Modify: `.index/bin/tests/test_index_assets.py` - add `TestDesignTokens`

## Tests First

Add `TestDesignTokens` asserting against `ia.HTML_TEMPLATE`:

1. `test_both_schemes_define_every_token` - each of the 11 token names appears in the `:root`
   block and in the `prefers-color-scheme:dark` block
2. `test_no_pure_black_or_white_in_dark_scheme` - the dark block contains neither `#000` nor
   `#fff` nor `#ffffff`
3. `test_single_radius_scale` - collect every `border-radius:` value; the set is a subset of
   `{var(--r), var(--r-sm), 50%}`
4. `test_tabular_numerals_present` - `tabular-nums` appears at least 4 times
5. `test_no_em_dash_anywhere_in_template` - `HTML_TEMPLATE` contains neither `U+2014`
   (em dash) nor `U+2013` (en dash). Match by codepoint, not by pasting the glyph.
   **This test guards every later phase too**

### Verified: the template already ships 8 em-dashes

Test 5 fails immediately against existing strings. Six UI strings must be rewritten, at these
template-relative lines:

| Line | Current | Replace with |
|---|---|---|
| 195 | `"— author pending —"` | `"author pending"` |
| 201 | `"— category pending —"` | `"category pending"` |
| 248 | `a.local_name:"—"` | `a.local_name:"-"` |
| 249 | `a.author||"—"`, `catPath(a)||"—"`, `v.version||"—"` | `"-"` in all three |

**Safe against the guarded tests.** `test_store_url_has_a_slot_in_both_views` (526) asserts the
substring `category pending`, which the current `"— category pending —"` contains and the
replacement still contains. No test references `author pending` or the table placeholders.

### Regression guards that must stay green untouched

- `test_indent_rule_exists_for_every_depth_the_store_produces` (line 510) requires
  `.facet.lvl1{` through `.facet.lvl4{` after whitespace stripping. **Keep those four
  selectors verbatim.** Only their declarations may change
- `test_no_external_resource_loading` (384) requires no `@import`

## Implementation Steps

1. Write the five new tests. Confirm 3, 4, 5 fail and 1, 2 fail against the current CSS
2. Replace the token block with the two-scheme system above
3. Restyle base elements: `body`, `header`, `#q`, `button`, `aside`, `.facet`, `.card`,
   `.tag`, `.badge`, `table`, `td`, `th` against the new tokens
4. Apply the radius scale. Delete every hardcoded `3px`/`4px`/`5px`/`9px` radius
5. Add `tabular-nums` to `.count`, `.facet .n`, card size line, table numeric cells
6. Collapse badge classes to `.badge` and `.badge.warn`. Delete `.dup`, `.ns`, `.pend`
   rules; Phase 3 removes their JS call sites
7. Set density 7 spacing: rail 220px, grid gap 10px, card padding 8px, table row ~28px
8. Add a visible focus ring using `--accent` on every interactive element
9. Run the full suite

## Success Criteria

- [ ] Five new tests pass
- [ ] `test_indent_rule_exists_for_every_depth_the_store_produces` still passes
- [ ] Full suite green
- [ ] Regenerate `index.html`, open from `file://`, confirm both schemes via system toggle
- [ ] Body text contrast checked at AA in both schemes
- [ ] Keyboard tab shows a visible focus ring on search, buttons, and facets

## Risk Assessment

| Risk | Mitigation |
|---|---|
| Badge class removal breaks JS before Phase 3 lands | Keep the JS call sites emitting `.badge`; unknown modifier classes degrade to the neutral chip rather than erroring |
| Whitespace change breaks the `.facet.lvlN{` match | That test strips spaces before matching, so only selector text matters. Do not reformat into a grouped selector |
| Dark-scheme contrast regression | Check `--ink-2` and `--ink-3` against `--bg` and `--surface`, not just `--ink` |
