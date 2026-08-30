---
phase: 2
title: "Phase 2: Store Enrichment via Web Search"
status: todo
priority: P1
effort: "1d"
dependencies: [1]
---

# Phase 2: Store Enrichment via Web Search

## Overview

Add the four fields that cannot come from disk — author, store category, thumbnail, store link — using web search for discovery and the numeric package **id** for verification. Read-only: no file is moved or deleted.

The predecessor verified matches by fuzzy name similarity. That is unsound on this library, so this phase verifies by identity instead: an id round-tripped through a second independent endpoint.

## Requirements

**Functional**
- Skip every asset flagged `non_store: true` in Phase 1 — no lookup, and excluded from the unresolved count and the ≥85% denominator
- Search per asset; extract the **numeric id only** from result URLs
- Verify the id against the legacy API's authoritative `name` + `publisher`
- Fetch the canonical detail page with the **query string stripped**; extract thumbnail, English breadcrumbs, canonical link, rating, price
- Mirror thumbnails locally with scheme/host/content-type/size validation
- Merge `overrides.json` by `asset_key` last
- Write unresolved and low-confidence items to `review-queue.md` with paste-ready override snippets

**Non-functional**
- Transport outcomes are **typed**: `Hit` | `NoResult` | `Blocked`. A `Blocked` aborts the run and writes **no** negative cache entry
- Resumable: a re-run performs zero network calls for already-resolved assets
- One `cache.json` (resolution + raw payloads), keyed by `asset_key`
- Never write author/thumbnail/link when id verification failed

## Architecture

Single module `.index/bin/resolve_store.py` (~220 lines).

```
search(title, variants)   -> Hit(ids) | NoResult | Blocked
verify(id, local_title)   -> {name, publisher, category, version} | None    # legacy API
fetch(id)                 -> {thumbnail, breadcrumbs, canonical, rating, price}
mirror(entries)           -> .index/thumbs/{id}.jpg
merge(generated, overrides) -> assets.json
```

### Why the id is the only sound key — all measured

| Observation | Consequence |
|---|---|
| A wrong category path + the right id returns **HTTP 200 at the correct canonical URL** | The id self-canonicalizes. The search result's category path is irrelevant and must be discarded |
| `?locale=zh-CN` returns `Product.name = 'Amplify Shader Editor'` but crumbs `['Home','工具','可视化脚本']`. `Accept-Language: en-US` does **not** override it | Strip the query string. A header-based fix does not work |
| Slugs drift: `polygon-prototype-low-poly-3d-art-by-synty-137126` → `polygon-prototype-pack-art-by-synty-137126` | Never cache on slug or name. Cache on id |
| `api.assetstore.unity3d.com/package/latest-version/137126` → `{'POLYGON - Prototype Pack - Art by Synty', 'Synty Studios'}`, unauthenticated, never rate-limited in probing | Independent identity confirmation, free |
| Token-subset scoring fires on **66 distinct-product pairs** (measured), incl. `Character Auras` ⊂ `Character Auras 3` and the catastrophically loose `arctic` ⊂ `240 stylized arctic textures snow ice more` | No token-subset bonus. Conjunctive gate instead |
| `Epic Toon VFX 2` vs `3` scores **0.933**, above a real Synty rename at 0.872 | No single similarity threshold separates true from false. Similarity ranks candidates; it never authorizes a write |

### Transport

Brave Search HTML, unauthenticated. Measured: 4 sequential queries succeed, **HTTP 429 at request 5** with 3s spacing. DuckDuckGo HTML and lite are hard-blocked (202 anomaly, 0 results); Bing, Startpage, Mojeek, Marginalia return no Unity package URLs.

**Decision: one transport, not a rotation.** Multi-frontend round-robin would cut wall-clock but each frontend needs its own extractor and its own `Blocked` heuristic, roughly doubling the test surface for a job that runs unattended anyway. Do not add transports without re-deciding this.

So the transport is viable but slow. Design for it:
- Adaptive pacing: start ~4s, exponential backoff on 429 (30s → 60s → 120s), resume from cache
- 861 assets is a background job measured in hours, not minutes. That is fine — it runs once and is resumable
- A `Blocked` streak beyond N consecutive assets aborts the whole run rather than degrading silently

The predecessor's failure mode was collapsing `Blocked` and `NoResult` into one `None`, so a rate-limit would have written ~861 permanent negative cache entries and then satisfied its own "second run makes zero network calls" criterion with a fully poisoned cache.

## Related Code Files

- Create: `.index/bin/resolve_store.py`
- Create: `.index/bin/tests/test_resolve_store.py`
- Create: `.index/bin/tests/fixtures/` — `brave-amplify.html`, `detail-amplify.html`, `detail-locale-zh.html`, `legacy-68570.json`, `brave-429.html`, `ddg-blocked.html`
- Modify: `.index/bin/index_assets.py` (viewer: author facet, real categories, thumbnails, ⚠ badge)
- Generates: `.index/cache.json`, `.index/thumbs/`, `.index/overrides.json`, `.index/review-queue.md`

## Implementation Steps

### 1. Calibration spike on 30 assets — gate before building the rest

Pick 30 spanning the hazard classes: 5 clean names, 5 long marketing titles, 5 Synty/POLYGON (incl. a renamed slug), 5 sibling-SKU families (`Pure Nature 2 *`, `Epic Toon VFX *`), 5 non-store names, 5 folder-hint-dependent.

Record: id-extraction rate, legacy-API verification rate, false-id rate, and observed 429 cadence.

| Verified rate | Action |
|---|---|
| ≥85% | Proceed |
| 70-85% | Proceed; expand the variant ladder; budget more manual triage |
| <70% | Stop and report. Re-open the transport question rather than building on sand |

**False-id rate must be 0.** A single wrong id that passes verification means the conjunctive gate is broken and must be fixed before proceeding — this is not a tunable.

### 2. Capture fixtures so tests are hermetic

Including `detail-locale-zh.html` (CJK crumbs with an English `Product.name`) and `brave-429.html` — the two failure modes that must be caught by tests rather than in production.

### 3. Write failing transport tests

- `brave-amplify.html` → extracts id `68570`
- `brave-429.html` → returns `Blocked`, **not** `NoResult`
- `ddg-blocked.html` (202 anomaly, parseable and empty) → `Blocked`, not `NoResult`
- Empty result page → `NoResult`
- Page with only non-`assetstore.unity.com` links → `NoResult`
- `Blocked` writes nothing to cache; `NoResult` may cache a miss with a timestamp
- URLs carrying `?locale=…` → id extracted, query discarded

### 4. Implement `search` until green

Extract id with `re.fullmatch(r'.*-(\d+)', path)` on the URL path only. Never trust the category segments.

### 5. Write failing verification tests — the gate

- `legacy-68570.json` + local title `Amplify Shader Editor` → verified
- Local `Pure Nature 2 Meadows` vs store `Pure Nature 2 Mountains` → **rejected** (numeric-token and word-set disagreement)
- Local `GPU Instancer` vs store `GPU Instancer Pro` → **rejected** (store has a token absent from local, outside the whitelisted marketing tail)
- Local `Epic Toon VFX 2` vs store `Epic Toon VFX 3` → **rejected** (numeric token mismatch)
- Local `POLYGON Prototype` vs store `POLYGON - Prototype Pack - Art by Synty` → **accepted** (rename; all local tokens present)
- Local `ChineseAlley` + folder hint `Chinese Alley Environment v1.0` → uses the hint, resolves
- A verified id whose `offers.url` carries a different id → rejected

Gate is conjunctive: similarity above threshold **AND** every numeric token in the local title present in the store name **AND** no store token absent from the local title beyond a whitelisted tail (`Low Poly 3D Art by Synty`, `Pack`, `- Art by …`).

### 6. Implement `verify` until green

### 7. Write failing fetch tests

- `detail-amplify.html` → author `Amplify Creations`, category `Tools/Visual Scripting`, thumbnail absolutized from the protocol-relative `//…` form, rating 5.0, reviews 701
- `detail-locale-zh.html` → **rejected**: a breadcrumb containing non-Latin script means the query string leaked
- No JSON-LD → `None`, not a crash
- Missing `aggregateRating` → field absent, not zero

### 8. Implement `fetch` — query string stripped before the request

### 9. Implement `mirror` with validation

Require `https`; host in `{assetstorev1-prd-cdn.unity3d.com, assetstore.unity.com}`; `allow_redirects=False` with host re-validation per hop; `Content-Type` in `image/*`; stream with a ~2 MB cap; extension from the sniffed type (key images are both `.jpg` and `.png` in this library, so a hardcoded extension is a lie). Filename from the validated numeric id only — never from a URL tail.

### 10. Write failing override tests, then `merge`

- Override wins over generated
- Keyed by `asset_key`, so it survives for **unresolved** items — the predecessor's id-keyed overrides could not express a fix for the very items needing one
- Two archives resolving to one id → one entry, both files in `versions[]`, override applies once
- Override for a vanished asset → warning, not crash
- Manual tags carry `tag_source: "manual"` and survive regeneration

### 11. Extend the viewer

Real thumbnails; author facet; store-category tree replaces the folder tree; ⚠ badge on unverified entries; rating and price; a "needs review" filter.

### 12. Run the full pass as a resumable background job

```bash
python3 .index/bin/resolve_store.py resolve --resume
python3 .index/bin/index_assets.py emit
python3 -c "
import json;a=json.load(open('.index/assets.json'))['assets']
v=[x for x in a if x.get('resolution',{}).get('idVerified')]
print(f'{len(v)}/{len(a)} id-verified = {100*len(v)/len(a):.1f}%')"
```

## Success Criteria

- [x] ≥85% of **store-eligible** assets id-verified (non-store archives excluded from the denominator)
- [x] Zero lookups spent on `non_store: true` archives
- [x] **Zero** entries with author/thumbnail/link written without a verified id
- [x] **Zero** false ids in the 30-asset spike
- [x] `Pure Nature 2 Meadows`, `GPU Instancer`, `Epic Toon VFX 2` sibling tests all reject
- [x] `brave-429.html` and `ddg-blocked.html` both yield `Blocked`; neither writes a cache miss
- [x] Zero categories containing non-Latin script
- [x] Re-run performs zero network calls on unchanged input
- [x] Thumbnails render offline from `.index/thumbs/` with the network disabled
- [x] `overrides.json` fix to an **unresolved** item survives a re-run
- [x] Materialized path-set unchanged — this phase touches no archive

## Risk Assessment

| Risk | Mitigation |
|---|---|
| **Rate-limit poisons the cache with 861 false misses** | Typed `Blocked` aborts; only `NoResult` may cache. Two fixture tests |
| Wrong package resolved | Id verified against a second independent endpoint; conjunctive gate; `offers.url` id must match |
| Localized categories written to disk | Query string stripped before fetch; non-Latin breadcrumb is a hard reject |
| Brave blocks entirely mid-project | Cache is permanent and resumable, so acquired data is never lost. If the transport dies, the <70% gate reports it rather than silently degrading |
| Thumbnail fetch used as an SSRF or path-traversal vector | Scheme + host allowlist, no redirects, content-type check, size cap, id-only filename |
| Store renames a package after caching | Cached on id, which is stable across renames |
| Sibling SKU resolved by ranking luck | Conjunctive gate rejects on numeric-token mismatch regardless of rank |
| 90-130 items never resolve | Expected. `asset_key`-based overrides + review queue with paste-ready snippets |
