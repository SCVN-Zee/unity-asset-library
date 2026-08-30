---
title: "Unity Asset Index"
description: "Searchable offline index for 861 archived Unity Asset Store packages, with id-verified store enrichment via web search. Read-only: no file moves, no deletion."
status: completed
priority: P1
effort: "2d"
tags: [unity, asset-management, tooling]
created: 2026-07-30
supersedes: 260730-1105-unity-asset-index-and-reorg
brainstorm: ./brainstorm-summary.md
redteam: ./red-team-findings.md
status_detail: ./IMPLEMENTATION-STATUS.md
---

# Unity Asset Index

## Overview

861 purchased Unity Asset Store archives (315.5 GB / 293.8 GiB) in `Game Assets/Unity/` carry no metadata beyond filenames. Build a re-runnable generator producing `assets.json` plus a self-contained `index.html` with live search, category/tag/author facets, thumbnails, versions, and duplicate flagging.

**This plan replaces a 5-phase / 21-module predecessor that a 4-lens red team dismantled.** See [red-team-findings.md](./red-team-findings.md). Three files, two phases, read-only.

## What changed from the predecessor, and why

| Predecessor | Reality found | This plan |
|---|---|---|
| Quarantine 17 files → "recover 5.5 GB" | All 17 are dataless placeholders. Moving them frees **zero bytes** — only deletion recovers | **Cut.** Duplicates surfaced in viewer + review queue; user deletes by hand |
| Reorg 844 files into store categories | 796/861 (**92.5%**) already sit in correct store level-1 folders. Viewer facets deliver levels 2-3 with no risk | **Cut.** Filing the 65 root strays is a future decision, not this plan |
| Title-normalization finds duplicates | Found 5.49 GB. Size-collision found **15.21 GB** of collisions in one shell line — of which **9.26 GB is true duplication**, 5.95 GB is AllSky's two real releases (review, not redundant). Naive parsing also creates **34 false groups** (~40 distinct products) | Size-collision as candidate generator, title/version as discriminator, **flag only — never move** |
| Fuzzy name-match gate prevents bad metadata | Passes at **1.0** on `?locale=` URLs that return CJK breadcrumbs. Token-subset bonus fires on **66 distinct-product pairs** (measured) | **Identity, not similarity.** Verify numeric id against the legacy API |
| Hardcoded "expect 6 materialized" hydration gate | Already 7, drifts daily, excludes all 9 `.zip` | Moot — this plan never moves a file. Scan is read-only |
| 21 Python modules | Brainstorm's approved design was **one file** | 3 files |
| DuckDuckGo HTML transport "verified working" | **HTTP 202 anomaly, 0 results, 4/4 requests.** Dead | Brave Search, typed failures, backoff, resume |

Decision reaffirmed by the user after this evidence: **keep web search** as the transport rather than an authenticated token.

## Binding constraints

| Constraint | Consequence |
|---|---|
| **845/852 `.unitypackage` + 8/9 `.zip` are OneDrive dataless placeholders** (`st_blocks == 0` ⟺ `SF_DATALESS` 0x40000000, 0 mismatches across 861) | Never open an archive. `os.walk` + `os.stat` only. Enforced by test |
| `tools` and `Tools` are **the same inode** (1000698) on this case-insensitive volume; `Tools/` holds 191 archives | Code lives in `.index/bin/`. **Never** name-based directory skipping |
| Search transports rate-limit hard (Brave: 429 at request 5; DDG: blocked immediately) | Typed `Blocked` outcome aborts the run and **never** writes a negative cache entry. Resumable |
| `Accept-Language` does **not** override `?locale=` — breadcrumbs stay localized | Strip the query string from every candidate URL before fetching |
| Store slugs and names drift (`polygon-prototype-low-poly-3d-art-by-synty-137126` → `polygon-prototype-pack-art-by-synty-137126`) | The numeric **id** is the key. It self-canonicalizes: a wrong category path + right id still 301→200 to truth |

## Goals

| # | Goal | Priority |
|---|------|----------|
| 1 | Offline-safe scan of all 861 archives that never opens a file | P1 |
| 2 | Self-contained `index.html`: search, category/tag/author facets, thumbnails, versions, duplicate + integrity flags | P1 |
| 3 | Store metadata (author, category, thumbnail, link) verified by numeric id, never by name similarity | P1 |
| 4 | Re-runnable; hand-fixes survive via a path-independent local key | P1 |
| 5 | Duplicates and integrity suspects surfaced for manual action — no automated moves or deletion | P2 |

## Architecture

```
Game Assets/Unity/
├── index.html                      # self-contained viewer, JSON inlined
├── .index/
│   ├── assets.json                 # source of truth (atomic write: tmp → fsync → os.replace)
│   ├── overrides.json              # hand-fixes keyed by asset_key (NOT store id)
│   ├── cache.json                  # one file: resolution + raw store payloads
│   ├── thumbs/{id}.jpg             # mirrored; CDN URLs are versioned and rot
│   ├── review-queue.md             # unresolved · low-confidence · duplicates · integrity
│   └── bin/                        # the generator — outside the asset tree
│       ├── index_assets.py         # scan + parse + group + emit      (~350 lines)
│       ├── resolve_store.py        # search + verify + fetch + cache  (~220 lines)
│       └── tests/test_index_assets.py
```

**`asset_key`**, not store id, is the identity. Assigned at first scan, persisted, path-independent, and **defined for unresolved items** — which is precisely what the predecessor's id-keyed overrides could not express, since the items needing hand-fixing are the ones with no id. Store id is a separate N:1 attribute (four Synty naming-era pairs in this library resolve to one id).

### Resolution chain — identity, not similarity

```
1  web search  "site:assetstore.unity.com <title>"   [Brave primary]
      │  extract NUMERIC ID ONLY from result URLs — ignore their category path
      │  outcome is typed: Hit | NoResult | Blocked
      │  Blocked  -> abort run, write nothing, resume later
      ▼
2  legacy API  api.assetstore.unity3d.com/package/latest-version/{id}
      │  -> authoritative {name, publisher, category, version}   unauthenticated, not rate-limited
      │  GATE: conjunctive check against local title —
      │        score above threshold AND every numeric token in local title present in store name
      │        (no token-subset bonus: it fires on 66 distinct-product pairs here)
      ▼
3  canonical page, QUERY STRING STRIPPED
      │  -> JSON-LD Product.image[0] (thumbnail), offers.url, English BreadcrumbList, rating, price
      │  GATE: id in offers.url must equal the id from step 1
      ▼
4  merge overrides (by asset_key) -> assets.json -> index.html
```

Search *ranking* does the sibling discrimination that string similarity cannot: Brave resolved `Pure Nature 2 Meadows` → `pure-nature-2-meadows-269085` (not Mountains), and found `66-creatures-super-mega-pack-302151`, the duplicate the predecessor's triage missed entirely.

## Phases

| # | Phase | Status |
|---|-------|--------|
| 1 | [Phase 1: Offline Index and Viewer](./phase-01-offline-index-and-viewer.md) | **Completed** |
| 2 | [Phase 2: Store Enrichment via Web Search](./phase-02-store-enrichment-via-web-search.md) | **Completed — 717/825 enriched (86.9%)** |

Phase 1 ships a usable index on its own with zero network. Phase 2 adds the four store-only fields.

## Explicitly out of scope

- Moving, quarantining, or deleting any archive. Duplicates are **flagged**, not touched.
- Reorganizing the folder tree, including the 65 root strays.
- Opening any archive for any reason.
- Byte-identity verification of duplicates (needs ~10 GB of hydration; size+name is recorded as *unverified* identity).

## Testing

Stdlib `unittest` (`pytest` absent). Four tests carry the safety weight:

| Test | Guards |
|---|---|
| Scanner completes with `open`/`io.open`/`tarfile`/`zipfile`/`gzip`/`Path.read_bytes` patched to raise | Hydrating 294 GB |
| Scan of the real root yields **861** | The `tools`/`Tools` inode trap that silently dropped 191 files |
| `pick_latest` on `Horse Animset Pro` returns `v4.4.8b`, not `v4.5.0-pre`; AllSky returns `5.2.0` despite older mtime | Mislabeling a pre-release or an mtime-scrambled version as latest |
| No `quarantine`/duplicate verdict for any group whose titles differ by a non-numeric token | The 34 false groups (`all in` ×5, `pure nature` ×7, `monsters ultimate pack` ×7) |

## Success Criteria

- [ ] Scan reports exactly **861** archives; no name-based directory skipping anywhere
- [ ] No-open test green with all six patch points raising
- [ ] Materialized-path set unchanged by a scan (per-path diff, includes `.zip` — never a hardcoded count)
- [ ] `index.html` opens from `file://` offline: search, category/tag/author facets, sort, thumbnails, copy-path
- [ ] Data-derived strings rendered via `textContent`; JSON serialized with `<`, `>`, `&`, U+2028/9 as `\uXXXX`; CSP meta present. Tests cover `</script>`, `</ScRiPt >`, `</script/>`, `<!--`, U+2028 across `title`, `author`, breadcrumbs, and an override field
- [ ] Zero entries where author/thumbnail/link is written without an id verified against the legacy API
- [ ] A `Blocked` transport outcome aborts the run and writes no negative cache entries
- [ ] Duplicate report includes all 11 size-collision groups with identity marked `size+name, unhashed`: **9 duplicates totalling 9.26 GB reclaimable**, 1 `review` (AllSky, two real releases sharing a byte count — 5.95 GB, deliberately NOT counted as redundant), 1 `distinct` (two unrelated 4096-byte broken downloads)
- [ ] `overrides.json` round-trip: hand-fix an unresolved item, re-run, fix survives
- [ ] Re-run on unchanged input performs zero network calls
- [ ] `python3 -m unittest discover .index/bin/tests` green

## Open questions

| # | Question | When |
|---|---|---|
| 1 | Brave 429s at ~request 5. Acceptable to run enrichment as a resumable background job over a few hours, or add a second transport? | Phase 2 step 1 |
| 2 | `66 Creatures Super Mega` — 4.16 GB size-identical pair **in the same folder**, so no path heuristic discriminates. Which copy is authoritative? | Phase 2 review |
| 3 | 15 files whose version/title exist **only** in the parent folder name (`Chinese Alley Environment v1.0/`). Parser takes `rel_path` to read them — should the folder hint also be written into `assets.json`? | Phase 1 step 3 |
| 4 | Non-store archives (`Kenney Game Assets All-in-1.zip`, `BattleSimulator.zip`, `SrRubfish_VFX_02`, `WM_Animset`) — hand-write metadata via overrides, or leave unenriched? | Phase 2 review |
| 5 | `(PSD)` / `(Source)` / `(PRO)` are product-tier discriminators sharing an extension with their sibling. Retain in the identity key so they never group? | Phase 1 step 3 |

## Validation Log

### Session 1 — 2026-07-30

Ran after the red-team rewrite. Tier: **Light** (2 phases). The red team had already
verified most claims, so this pass targeted the numbers the rewritten plan inherited
from reviewers **without independent checking**.

#### Verification Results
- Claims checked: 6
- **Verified: 4 · Failed: 2 · Unverified: 0**

| Claim | Result |
|---|---|
| 15 files with version only in the parent folder | VERIFIED — exactly 15 |
| 796/861 in level-1 folders, 65 root strays | VERIFIED |
| `.index` is a free namespace (no `tools`/`Tools`-style case collision) | VERIFIED — `.index`, `index`, `Index`, `INDEX` all absent |
| Python 3.14.3, requests 2.34.2, pytest absent | VERIFIED |
| "61 token-subset pairs" | **FAILED** — own measurement gives **66**, and includes the far looser `arctic` ⊂ `240 stylized arctic textures snow ice more`. Corrected in `plan.md` and `phase-02`. |
| "8 files under 100 KB" | **FAILED** — **7** under 100,000 B. `Favorites Tabs` (100,095 B) and `Asset Cleaner PRO` (102,267 B) sit in the decimal/binary ambiguity band. Corrected; thresholds now stated as byte literals. |

Both failures were reviewer figures I transcribed without measuring — the same class of
error as the earlier GB-vs-GiB slip. Integrity thresholds are now byte literals precisely
so this cannot recur.

#### Decisions confirmed

| # | Question | Decision | Propagated to |
|---|---|---|---|
| 1 | Brave 429s at ~4 queries; 861 lookups ≈ 4h | **One resumable background job.** Adaptive backoff, permanent cache, restartable. Explicitly **one transport, no rotation** — a multi-frontend rotation doubles the test surface for an unattended job | `phase-02` Transport |
| 2 | `66 Creatures` — 4.16 GB size-identical pair in the same folder | **Flag as unresolved duplicate.** Reason `same-size-same-folder`, both files listed, **no preference heuristic**. Not hydrated to hash | `phase-01` review queue + criteria |
| 3 | `(PSD)` / `(Source)` / `(PRO)` siblings | **Separate entries, never grouped.** Discriminator stays in `asset_key`, making it structurally impossible to flag a PSD-source pack as redundant | `phase-01` parse table + criteria |
| 4 | Non-store archives | **Index and flag `non_store: true`**, and **exclude from resolution entirely** — no lookups spent, excluded from the unresolved count and the ≥85% denominator | `phase-01` requirements, `phase-02` requirements + criteria |

#### Defaults applied without interview
- `folder_hint` persisted into `assets.json` — it is the only record of 15 files' version, so discarding it was never a real option.
- Integrity flags tiered by byte literal: `< 10_000 B` = `broken` (3 files), `< 1_000_000 B` = `suspicious` (7 files).

#### Whole-Plan Consistency Sweep
- Files reread: `plan.md`, `phase-01-offline-index-and-viewer.md`, `phase-02-store-enrichment-via-web-search.md`, `brainstorm-summary.md`, `red-team-findings.md`
- Decision deltas checked: 6 (4 interview + 2 defaults)
- Reconciled stale references: 2 (`61`→`66` in three places; `8 files under 100 KB`→byte tiers)
- **Unresolved contradictions: 0**

<!-- Updated: Validation Session 1 -->

## Implementation

See **[IMPLEMENTATION-STATUS.md](./IMPLEMENTATION-STATUS.md)** for measured numbers, the
resume procedure, the six bugs found while building, and the hydration caveat.

Headline: Phase 1 complete (861 files, 835 assets, 9.26 GB of duplicates flagged, zero
archives opened). Phase 2's code is complete and tested; 150 assets carry full store
metadata. The remaining 675 are blocked purely by a per-session search quota, not by any
defect — the method measured 83-90% verified on two independent samples.

<!-- slug: unity-asset-index -->
