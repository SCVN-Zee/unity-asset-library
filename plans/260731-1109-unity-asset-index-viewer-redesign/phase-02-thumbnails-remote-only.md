---
phase: 2
title: "Thumbnails Remote-Only"
status: completed
priority: P1
effort: "2h"
dependencies: [1]
---

# Phase 2: Thumbnails Remote-Only

<!-- Updated: Validation Session 1 - stop mirroring in resolve_store.py added to scope -->

## Overview

Switch the viewer from mirrored local thumbnails to the CDN URL, tighten the CSP to that one
host, add lazy-loading attributes, make the imageless card a designed state rather than a grey
box reading "no image", and stop the generator writing new local mirrors.

## Stop mirroring (validation decision)

`resolve_store.py` currently downloads and writes a local copy for every newly-enriched asset.
Stop that, but **do not delete `mirror_thumbnail` itself.**

**Change the call sites, not the helper.** `mirror_thumbnail` is invoked from three places:
`attach_detail` at lines 582-588 and 608-614, and once more at line 1035. Remove those
invocations so records carry `thumbnail_remote` only.

**Why the helper stays:** it has roughly 8 dedicated tests (`test_resolve_store.py:241-290`)
covering its host allowlist, `http://` rejection, and `file:///etc/passwd` rejection. That
validation is hard-won. With Phase 6 deleting the existing cache, re-mirroring is the
documented reversal path if this decision is ever revisited, so the helper is retained
deliberately rather than left as accidental dead code. Its tests stay green untouched.

**Residue, handled in Phase 6:** existing cache entries keep their `thumbnail_local` values,
and `build()` keeps copying them into `assets.json` as `thumbnail.local`. Nothing reads them
after this phase. Phase 6 removes them so the cache deletion leaves no dangling paths.

## Requirements

**Functional**
- Card image source is `a.thumbnail.remote`; `a.thumbnail.local` is no longer read
- Missing thumbnail and failed load render the same designed placeholder
- Placeholder shows the asset's level-1 category, not the string "no image"
- Images carry `loading="lazy"`, `decoding="async"`, and explicit `width`/`height`

**Non-functional**
- CSP allows images from exactly one host plus `data:`, nothing else external
- Images continue to be created via `document.createElement("img")`, never static markup

## Architecture

### Measured facts behind this phase

- All 717 thumbnails have a remote URL. Zero assets lose an image
- Single host `assetstorev1-prd-cdn.unity3d.com`, already allowlisted at
  `resolve_store.py:158` (`THUMB_HOSTS`)
- **The CDN does not resize.** `?width=320` and `?w=320` both return an identical
  `851,782` bytes. Raw GCS bucket, not an image CDN. Do not add resize params
- `Cache-Control: public, max-age=2591000` (~30 days), so repeat loads are cache hits

### Consequence this phase exists to absorb

Offline stops working. Design goal #1 of the predecessor plan was `file://` with no server.
With no network, all 835 cards render imageless. That is why the placeholder is designed
rather than patched: it is now a whole-page state, not an edge case for 118 assets.

### CSP

```
default-src 'none'; img-src https://assetstorev1-prd-cdn.unity3d.com data:;
style-src 'unsafe-inline'; script-src 'unsafe-inline'
```

`'self'` drops out with the local thumbs.

### Placeholder

Sunk block, `--sunk` background, 1px `--line` border, showing the asset's level-1 category in
`--ink-3` small caps. Falls back to the literal `uncategorized` for the 12 assets with no
category. No hand-drawn SVG glyph: banned by house rules, and no icon library survives the CSP.

### Load-failure path

An `error` listener on each `<img>` replaces it with the same placeholder node, so offline and
CDN rot share one code path. Registered with `addEventListener`, not an inline `onerror`
attribute.

### Fixed dimensions

Thumb slot keeps `aspect-ratio:16/10`. Set `width="1950" height="1300"` on the element so the
browser reserves space from the intrinsic ratio before bytes arrive. Prevents layout shift.

## Related Code Files

- Modify: `.index/bin/index_assets.py` - CSP meta line, `card()` thumb block in `HTML_TEMPLATE`
- Modify: `.index/bin/resolve_store.py` - remove the three `mirror_thumbnail` call sites
  (lines 582-588, 608-614, 1035). **Keep the `mirror_thumbnail` function itself**
- Modify: `.index/bin/tests/test_index_assets.py` - amend `test_no_external_resource_loading`,
  add `TestRemoteThumbnails`
- Modify: `.index/bin/tests/test_resolve_store.py` - add `test_enrichment_does_not_mirror`.
  The existing `mirror_thumbnail` tests at 241-290 stay untouched and green

## Tests First

Add `TestRemoteThumbnails`:

1. `test_card_reads_remote_not_local` - `thumbnail.remote` present in template,
   `thumbnail.local` absent
2. `test_csp_allows_exactly_the_cdn_host` - CSP meta contains
   `img-src https://assetstorev1-prd-cdn.unity3d.com data:` and no longer contains
   `img-src 'self'`
3. `test_images_are_lazy_and_sized` - `loading` with value `lazy`, `decoding` with value
   `async`, and both `width` and `height` set on the image element
4. `test_placeholder_uses_category_not_no_image` - the string `no image` is gone; the
   placeholder path references `levels`
5. `test_image_failure_falls_back_to_placeholder` - an `error` listener is registered
6. `test_no_local_fallback_path` - the failure path goes straight to the placeholder and never
   reads `thumbnail.local`. Validation decision: remote or placeholder, nothing in between

Add to `test_resolve_store.py`:

7. `test_enrichment_does_not_mirror` - `attach_detail` on a record with a `thumbnail_remote`
   leaves `thumbnail_local` unset and writes no file

### The deliberate amendment

`test_no_external_resource_loading` (line 384) currently asserts a blanket "nothing external".
It passes literally after this change, because `src` is assigned via `setAttribute` and never
appears as `src="http` in the template. **Passing by accident is worse than failing.** Rewrite
it to state the new posture explicitly:

- CSP meta still present
- No `@import`, no `<link ... href="http`
- `img-src` names exactly one host
- No `script-src` or `style-src` host beyond `'unsafe-inline'`

Leave a comment in the test recording that remote thumbnails were a deliberate trade of
offline capability for 303 MB of OneDrive-synced disk, and that CDN rot has no local fallback.

### Regression guard

`TestHtmlEscaping._injects` (318) counts `img`/`svg`/`iframe` start tags **outside** `<script>`
and currently gets 0. Adding a literal `<img>` to static markup breaks it. Keep using
`document.createElement("img")`.

## Implementation Steps

1. Write the seven new tests plus the amended `test_no_external_resource_loading`. Confirm the
   new ones fail
2. Update the CSP meta line
3. In `card()`, swap `a.thumbnail.local` for `a.thumbnail.remote`
4. Add `loading`, `decoding`, `width`, `height` via `setAttribute`
5. Extract the placeholder into a `thumbFallback(a)` helper returning a node built from
   `levels(a)[0]` or `uncategorized`
6. Call `thumbFallback` both when no thumbnail exists and from the `error` listener
7. Remove the three `mirror_thumbnail` call sites in `resolve_store.py`. Leave the function
   and its tests alone
8. Run both test files
9. Regenerate `index.html`

## Success Criteria

- [ ] Eight tests pass, including the rewritten external-resource test
- [ ] The ~8 existing `mirror_thumbnail` tests (`test_resolve_store.py:241-290`) still pass
      untouched
- [ ] `TestHtmlEscaping` still reports zero escaped tags
- [ ] Full suite green (179 tests before this phase adds to it)
- [ ] Online: thumbnails load, scrolling does not stall
- [ ] **Airplane mode: all 835 cards show the category placeholder, zero layout shift, no console errors**
- [ ] DevTools Network shows only viewport images requested on first paint, not 708
- [ ] A fresh `enrich` run on a queued asset writes no file into `.index/thumbs/`

## Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| **Offline is now a broken mode** | High | Accepted by the user. Placeholder designed as a first-class state |
| **CDN rot blanks images permanently** | Medium | `error` listener degrades gracefully. No recovery without re-fetch. `.index/thumbs` survives this phase as a manual escape hatch; Phase 6 removes it only after everything else is verified |
| Removing call sites leaves `mirror_thumbnail` unreferenced | Low | Deliberate. It is the documented reversal path and carries ~8 security tests. Recorded in this phase so a later reader does not delete it as dead code |
| CSP typo silently blocks every image | Medium | Test 2 asserts the exact directive. Verify in DevTools Console that no CSP violation is logged |
| `width`/`height` at intrinsic 1950x1300 confuses layout | Low | `aspect-ratio` on the container governs; the attributes only seed the pre-load reservation |
| Someone later re-enables local thumbs and hits a CSP block | Low | The amended test's comment records why `'self'` was dropped |
