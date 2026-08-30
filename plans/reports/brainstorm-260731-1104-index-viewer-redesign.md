---
title: "Unity Asset Index Viewer Redesign"
type: brainstorm
date: 2026-07-31
status: approved
follows: 260730-1746-index-change-tracking
target: .index/bin/index_assets.py (HTML_TEMPLATE, lines 679-978)
tags: [unity, asset-management, frontend, redesign]
---

# Unity Asset Index Viewer Redesign

## Problem

Viewer for 835 assets / 861 files / 315.5 GB works but has real defects: 303 MB of
thumbnails loaded eagerly, full re-render per keystroke, facet counts that ignore the
active filter, cards carrying 8+ text rows and 4 buttons in a 190px column. Visual
language is the LLM-default warm-cream palette, reached for rather than decided.

Ask: redesign the front end, apply taste-skill craft rules, switch thumbnails to
remote-only.

## Binding constraints (all verified)

| Constraint | Consequence |
|---|---|
| `index.html` is **generated** from `HTML_TEMPLATE` at `.index/bin/index_assets.py:679-978` (299 lines: 51 CSS, 222 JS) | Editing `index.html` is wiped by next `scan`/`update`. All work lands in the template |
| CSP `default-src 'none'` | No web fonts, no CDN, no external stylesheets. Vanilla CSS + JS only |
| No build step, runs from `file://` | No React / Tailwind / Motion / shadcn / icon libraries |
| `test_rendering_uses_dom_apis_not_string_html` | `.innerHTML` banned, `textContent` required |
| `test_no_external_resources` | No `src="http` literal, no `@import`, no `<link href="http`, CSP meta must exist |
| Content tests | all-levels breadcrumbs, store + on-disk name, `generated` stamp, stale marking, pending-flag facet |
| `TestBuildDeterminism` | Do not thread new state into `build()` output |

## Data facts driving the design

- 835 assets, 861 files, 315.5 GB. 717 store-enriched (86%), 708 thumbs mirrored (85%)
- Categories: 7 level-1 → 43 level-2, depth to 4. Only **25 distinct tags**, **284 authors** (118 unknown)
- 552 rated (66%), 687 priced, 214/861 versions carry `release_date` (25%)
- 65 assets >1 GB, 12 >5 GB, largest 21.9 GB. 11 dup groups, 18 dup-flagged versions, 7 integrity flags
- Thumbs: 708 files, 303 MB, every one **1950×1300**, p50 392 KB, rendered into ~170px cards

## Audit of current viewer

| # | Finding | Evidence |
|---|---|---|
| 1 | 303 MB eager image load | no `loading="lazy"`, no `width`/`height`; ~130x oversampled by area |
| 2 | Full re-render per keystroke | `render()` rebuilds ~12,500 nodes + recomputes all facet counts, no debounce |
| 3 | Facet counts library-global | `facetCounts()` iterates `assets`, never the filtered list |
| 4 | Cards overstuffed | 8+ text rows in 190px: name, disk name, author, breadcrumb, version/size/files, rating, 5 tags, N badges |
| 5 | 3,104 focusable elements | 835x2 always-present actions + 717x2 store actions; tab order unusable |
| 6 | Date sort mostly dead | 75% of versions have null `release_date`, collapse to `""` |
| 7 | No rating sort; table headers styled `cursor:pointer` but unbound | 552 assets have a rating |
| 8 | Palette is AI-default warm cream | `--bg:#faf7f2`, one digit off a banned-default hex |
| 9 | No keyboard affordances, no URL state, author facet silently truncates 284 → 25 | |

## Taste-skill applicability (honest scoping)

Skill §13 excludes "dashboards / dense product UI / admin panels" and "data tables".
This viewer is exactly that. Its landing-page machinery does not apply.

**Applied:** typography discipline (§4.1), color calibration + consistency lock (§4.2),
card/elevation restraint + shape lock (§4.4), full interactive state cycles + contrast
(§4.5), content density (§4.9), theme lock (§4.11), perf/a11y guardrails (§6), dark mode
protocol (§8), AI-tell bans incl. zero em-dashes (§9), redesign protocol (§11).

**Not applied:** dials-for-landing-pages, design-system selection (§2, no npm), stack
(§3, impossible under CSP), hero/nav/bento/eyebrow/zigzag rules (§4.3, §4.7), image
strategy (§4.8, images ARE the data), testimonials (§4.10), scroll choreography (§5),
hero/marquee vocabulary (§10), block library (§12).

**Design Read:** offline single-user library browser for its owner, utilitarian instrument
language, native CSS + system font stack + strict density discipline.

**Dials:** `DESIGN_VARIANCE 3 / MOTION_INTENSITY 2 / VISUAL_DENSITY 7`. Deliberate
override of the 8/6/4 baseline: asymmetry fights a rail-plus-results tool, motion on 835
cards costs frames better spent on scroll.

## Approaches evaluated

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **A. Visual refresh only** | Zero behavior risk | Leaves findings 1-7 in place. Prettier, not better | Rejected |
| **B. Visual + perf** | Fixes 1-3, 8. Low risk, ~70% of value | Card overstuffing and 3,104-element tab order survive | Rejected |
| **C. Recompose shell + fix defects** | Fixes 1-7. Card and detail split is where design actually improves | Touches ~all 299 template lines | **Chosen** |
| **D. Full rework incl. data pipeline** | Adds virtualization, downscaled thumbs, restructured payload | Scope creep into generator scan/emit and thumbs cache | Rejected, user scoped to front end |

## Thumbnail decision: remote-only

User directive, reverses the predecessor plan's documented mirroring choice
(`thumbs/{id}.jpg # mirrored; CDN URLs are versioned and rot`).

**Measured before accepting:**
- All 717 thumbnails have a remote URL. Zero assets lose an image
- Single host: `assetstorev1-prd-cdn.unity3d.com`, already allowlisted at `resolve_store.py:158` (`THUMB_HOSTS`)
- **CDN does not resize.** `?width=320` and `?w=320` both return identical `851,782` bytes. Raw GCS bucket (`Server: UploadServer`, `x-goog-stored-content-length`), not an image CDN
- `Cache-Control: public, max-age=2591000` (~30 days)

**Buys:** 303 MB off a OneDrive-synced folder (cloud storage saving exceeds the local number).

**Costs:** offline stops working (plan goal #1 was `file://` with no server). CDN rot has no
local fallback: `key-image/{uuid}.jpg` changes UUID when a publisher updates key art.
Not a bandwidth win either, same bytes over WAN instead of local SSD; lazy loading plus the
30-day cache is what makes it acceptable.

**Design consequence, turned into an asset:** the imageless card stops being an edge case
(118 assets) and becomes a whole-page state (all 835). Card must hold up without its image,
which forces typography to carry the layout.

## Approved design

### Shell, three zones

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

Detail is a third column, not a modal: modals need focus trapping and lose your place in a
browse tool. Below 1100px it overlays. Below 760px the rail collapses to a disclosure.

### Card

| | Now | Proposed |
|---|---|---|
| Content | name, disk name, author, breadcrumb, version/size/files, rating, 5 tags, N badges, 4 buttons | image, name (2-line clamp), author, size |
| Focusable per card | 4 | 1 (whole card) |
| Rest of record | crammed in | detail panel |

Detail panel: both names, full breadcrumb, all tags, rating/reviews/price, every version
with its own size and flags, plus all four actions (open, copy path, store page, copy store URL).

Long names get `text-wrap: balance` + 2-line clamp, full string in `title`. Both native CSS.

### Imageless state, first-class

Empty thumb slot renders as a sunk block showing the asset's level-1 category in muted
small caps. Existing data, reads deliberate rather than broken. An `error` listener on every
`<img>` falls back to the same state, so offline and CDN rot share one path.

No hand-rolled SVG placeholder glyphs (banned, and no icon library survives the CSP).

### Palette, cool neutral + one accent

```
light   bg #fbfbfc   surface #ffffff   sunk #f4f4f6
        ink #18181b  ink-2 #52525b     ink-3 #a1a1aa
        line #e4e4e7 accent #2563eb
dark    bg #0c0c0e   surface #151518   sunk #0a0a0b
        ink #fafafa  ink-2 #a1a1aa     ink-3 #71717a
        line #27272a accent #3b82f6
```

No pure black or white. Accent **only** for active filter, focus ring, selected row. Never
decoration. Badge colors reduce from five to two: neutral chip for informational
(`non-store`, `variant`, `pending`), amber for genuine warnings (7 integrity flags).
Duplicates become a count in the detail panel, not a colored badge on every card.

**Shape lock:** 6px cards / inputs / buttons, 4px chips. No pills.

**Type:** `ui-sans-serif, -apple-system, "SF Pro Text", system-ui, sans-serif`.
`tabular-nums` on every size, count, and rating (today only on two selectors). Web fonts
impossible under `default-src 'none'`, not worth widening the CSP.

### Fixes folded in

| Finding | Fix |
|---|---|
| 1 | `loading="lazy"`, `decoding="async"`, explicit `width`/`height` (also kills CLS) |
| 2 | 120ms debounce + chunked append of 100 cards via IntersectionObserver sentinel |
| 3 | Counts from filtered set; each facet family excludes its own selection (standard faceted search) |
| 6 | Keep Date sort, nulls sort **last**, demoted below Rating |
| 7 | Add Rating sort; bind the table headers |
| 9 | `/` focus search, `Esc` clear or close detail, `↑↓` + `Enter` in table view |

### CSP

```
default-src 'none'; img-src https://assetstorev1-prd-cdn.unity3d.com data:;
style-src 'unsafe-inline'; script-src 'unsafe-inline'
```

Scoped to the one host already in `THUMB_HOSTS`. `'self'` drops with the local thumbs.

### Motion, dial 2

120ms on hover / active / focus. 160ms slide for the detail panel. Nothing else.
Killed under `prefers-reduced-motion: reduce`.

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **Offline is now a broken mode** | High | Imageless state designed as first-class, not a fallback. Accepted by user |
| **CDN rot silently blanks images over time** | Medium | `error` listener degrades to imageless state. No recovery without re-fetch |
| `test_no_external_resources` intent violated | Medium | Passes literally (`src` set via `setAttribute`, not baked in) but must be **amended deliberately**, not slipped past |
| Chunked render interacts badly with facet recount | Low | Recount runs on the filtered list once, before chunking |
| Detail panel + narrow viewport | Low | Overlay below 1100px, explicit per-breakpoint collapse |
| Template is a Python string literal, 299 lines | Low | Brace/quote escaping care; `__DATA__` placeholder must survive |

## Success metrics

- First paint renders without waiting on any network image
- Keystroke-to-repaint stays interactive at 835 items (debounce + chunking)
- Focusable elements in results drop from ~3,104 to ~835
- Facet counts change when a filter is applied
- Sort by Rating available; Date sorts nulls last; table headers respond
- Both themes pass WCAG AA on body text, AAA target on primary
- Zero em-dashes in any shipped UI string
- All existing viewer tests pass, except `test_no_external_resources`, amended deliberately

## Validation

1. `python3 .index/bin/tests/test_index_assets.py` green (minus the deliberate amendment)
2. Open `index.html` from `file://`: search, all four facet families, both views, sort, detail panel, copy actions
3. Airplane mode: every card renders its imageless state, layout does not shift
4. Both color schemes via system toggle
5. Keyboard only: `/`, `Esc`, `↑↓`, `Enter`, tab through a filtered result set

## Next steps

1. `/ak:plan` on this report
2. Implement in `HTML_TEMPLATE`
3. Regenerate `index.html`, verify against the validation list

## Unresolved questions

1. **Stop mirroring going forward?** Requires editing `mirror_thumbnail` in
   `resolve_store.py`. Generator work, outside "front end only". Alternative: leave mirroring
   running and let the viewer simply ignore `thumbnail.local`, keeping it as a dormant fallback.
2. **Delete the existing `.index/thumbs/` (303 MB, 708 files)?** Destructive, untouched.
   Recovery costs 708 rate-limited web fetches.
3. **URL-hash state for filters** was scoped out. Reload currently loses all selections.
   Worth a follow-up?
