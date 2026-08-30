# Unity Asset Store Archive — Searchable Index + Reorg

**Date:** 2026-07-30
**Status:** ⚠ **PARTIALLY SUPERSEDED — historical record, not the build spec.**
**Target:** `/Users/duyhuynh/Library/CloudStorage/OneDrive-Personal/Game Assets/Unity`

> **Read [plan.md](./plan.md) for what is actually being built.** A four-lens red team
> ([red-team-findings.md](./red-team-findings.md)) invalidated several designs recorded below.
> Do **not** implement from this document.
>
> | Recorded here | Now |
> |---|---|
> | §6 quarantine step (~5.5 GB recovery) | **Cut** — all 17 files are dataless placeholders, so moving them frees zero bytes |
> | §6 store-category reorg | **Cut** — 92.5% already in correct level-1 folders; viewer facets cover the rest |
> | §5 "all 27 duplicate groups triaged" | Incomplete — 11 size-collision groups, **15.21 GB**; `66 Creatures` (4.16 GB) missed |
> | §3 name-match confidence gate | Unsound — replaced by numeric-id verification |
> | §3 DuckDuckGo transport "verified working" | Dead (HTTP 202, 0 results) — replaced by Brave + typed failures |
> | §6 `tools/unity-asset-index.py` | Correct instinct (one file); code now lives in `.index/bin/` because `tools` **is** `Tools/` |
> | §2 "846/852 dataless, 6 materialized" | 845/852; **7** materialized, and it drifts daily |
> | §5 "1.9% of 293.8 GB" | 1.74% — decimal GB was divided by binary GiB |
>
> What remains valid and load-bearing: §2 (the OneDrive dataless constraint), §3 (the probed
> API surface — JSON-LD fields, the live legacy endpoint, the dead store APIs), §4 (filename
> parse hazards), and §5's per-group triage as *raw evidence* rather than as a complete census.

---

## 1. Problem statement

861 purchased Unity Asset Store archives (`.unitypackage` + 9 `.zip`), 293.8 GB logical, no metadata beyond filenames. Needs: searchable/filterable index carrying name, author, store category, tags, thumbnail, package path, store link, version. Plus: prune superseded versions, reorganize folder tree to mirror store taxonomy.

### Requirements (locked)

| # | Requirement | Decision |
|---|---|---|
| 1 | View surface | Self-contained `index.html`, JSON inlined |
| 2 | Store metadata resolution | Web search per asset, no auth |
| 3 | Row granularity | One entry per asset, `versions[]` nested |
| 4 | Lifecycle | Re-runnable generator + override layer |
| 5 | Reorg shape | 3 levels (store-native depth), publisher folders dissolved |
| 6 | Prune policy | Quarantine safe cases only; ambiguous → review queue |

### Acceptance criteria

- `index.html` opens offline, no network, no server. Renders all 861 archives.
- Live text search; facet filters on category tree, tags, author; sort by name/size/date/rating.
- Each card: thumbnail, name, author, category path, tags, version(s), size, store link, package path + copy-to-clipboard.
- Low-confidence resolutions carry a ⚠ badge and are listed in `review-queue.md`.
- Re-run rescans tree, reuses cache, re-applies `overrides.json`. Hand-fixes survive.
- Quarantine + reorg both emit an undo manifest.

### Out of scope

- Reading inside any archive (see §2 constraint).
- Editor-version detection, dependency graphs, license tracking.
- Cloud/multi-device sync UI beyond OneDrive's own file sync.
- Automated re-download of broken packages.

---

## 2. Binding constraint — OneDrive dataless placeholders

**846 of 852 `.unitypackage` files have `blocks=0`** — online-only placeholders. Only 6 materialized. `du` reports 509 MB local against 293.8 GB logical.

Consequence: **any read of archive contents hydrates the file.** A conventional indexer that opens each `.unitypackage` to inspect it would download ~294 GB. Every stage below uses `os.walk` + `os.stat` only. Non-negotiable.

Corollary for Phase 4: unverified whether macOS OneDrive File Provider moves a dataless file without hydrating. Phase 4 gate — move ONE small file, assert `stat -f %b` still `0`, abort otherwise.

---

## 3. Metadata sources (probed 2026-07-30)

| Source | Auth | Yields | Status |
|---|---|---|---|
| filename + `stat` | — | name, version, release date, size, mtime, path, pipeline variant | free, offline |
| `api.assetstore.unity3d.com/package/latest-version/{id}` | none | `{name, publisher, category, version, id}` | **200 live**. Not batchable (comma-list returns 1 object). Returns *legacy* taxonomy — `"Editor Extensions/Visual Scripting"` not `"Tools/…"` |
| store detail page `application/ld+json` | none | `Product.name`, `brand.name` (author), `image[0]` (thumb), `offers.url`, `BreadcrumbList` (current taxonomy), price, rating, reviewCount | **200 live**, primary source |
| `packages-v2.unity.com/-/api/purchases` | Bearer | user's exact purchase list w/ id, publisher, category, thumb | **401**, exists. Rejected per decision 2 |

Dead: `assetstore.unity.com/api/**` (all 404), `/api/graphql`, `head/package/{id}.json`. `/search` is client-rendered — unscrapable by curl. Sitemap lists publishers only (~24k), **no package URLs**. Slug-only and id-only URLs 404 — no redirect shortcut.

### Resolution accuracy trap (critical)

A fabricated-but-plausible URL returned **HTTP 200 for a different package**: `…/audio/music/orchestral/epic-orchestral-music-collection-149394` served breadcrumb `Tools / Physics / Limitless Gravity 2D`. Wrong guesses fail *silently with wrong data*.

**Mitigation, mandatory:** after fetch, fuzzy-match returned `Product.name` against parsed filename. Store `resolution.confidence`. Below threshold → ⚠ badge + review queue. Never write author/thumbnail/link from an unverified match.

### Category depth is mixed

Breadcrumbs return 2 **or** 3 levels depending on section:
- `Tools / Visual Scripting` → 2
- `3D / Environments / Fantasy` → 3
- `2D / GUI / Icons` → 3

Reorg tree is therefore **mixed-depth, store-native** — not forced to 3. Use breadcrumb verbatim; prefer URL-path/breadcrumb over the legacy API's stale taxonomy.

---

## 4. Filename parse quality (measured, all 861)

- 88% carry a version token (`v1.2.3`, `vv1.96`, `2025.3`, `(v1.0)`); 12% none.
- 840 unique cleaned names; **20 duplicate-name groups** (strict matcher), 27 (loose matcher).
- Long names are the literal store titles — helps matching, e.g. `Umbra Soft Shadows - Better Directional Contact Shadows for URP`.

### Known parse hazards

| Hazard | Example | Handling |
|---|---|---|
| Illegal `/` stripped from store title | `HDRPBuilt-inURP Medieval Fantasy Ruins…` ← `HDRP/Built-in/URP …` | re-insert candidate separators before search |
| Parenthesized version | `Creepy Animatronic Anims (v1.0)` | extend version regex |
| Build-variant suffix | `…_URP_2021.3.6f1`, `…_HDRP_…`, `…_Builtin_…` | parse to `pipeline` field, strip from title |
| `(REPACK)`, `(URP version)` | `POLYGON - Elven Realm … (REPACK)` | strip, retain as flag |
| Non-store names | `SrRubfish_VFX_02`, `WM_Animset`, `BattleSimulator.zip`, `Kenney Game Assets All-in-1.zip` | unresolvable → `_Unresolved/` |
| Double-dot typo | `Stylized Azure Hillside_URP_2021.3.6f1..unitypackage` | tolerate |
| Delisted packages | — | no URL exists, ever → override layer |

Expect **~10-15% (~90-130 items) needing manual triage.** Not a solvable-by-automation residue; the override layer is the answer.

---

## 5. Duplicate-group classification (all 27 groups triaged)

Totals: **16 groups → 17 moves** are safe (§5a + §5b); **11 groups** are ambiguous (§5c). 16 + 11 = 27.

### 5a. Safe — clean semver pairs, quarantine older (9 groups → 10 moves, ~505 MB)

`2D Pixel Unit Maker - SPUM` 1.7.7▸1.6.5 · `COZY Stylized Weather 3` 3.6.2▸3.6.0 · `DunGen` 2.18.5▸2.16.0 · `Easy Collider Editor` 6.18.7▸6.18.4 · `Magic Animation Blend` 3.2.1▸2.2.2▸2.1.0 · `Motion Warping Climb Interact` 3.2.0▸3.1.0 · `RPG VFX Bundle` 5.2.4▸5.1.0 · `Scriptable Sheets` 1.8.0▸1.5.1 · `Shader Graph Baker` 2025.3▸2025.1

### 5b. Safe — byte-identical copies in two folders (7 groups → 7 moves, ~4.99 GB)

`Pro Sound Collection` v1.3 (2121 MB, root + `Audio/`) · `Medieval Fantasy SFX Bundle` v1.0 (1618 MB, root + `Audio/`) · `Martial Arts Fight Game` v2.2 (617 MB) · `Insane AirComboSet` v1.1 (582 MB) · `Survival Animations` v1.0 (19 MB) · `Animation Preview Pro` v1.9.0 (18 MB) · `Creepy Animatronic Anims` v1.0

Same version + same size. Keep the copy whose location matches resolved store category.

**Total safe recovery ≈ 5.5 GB of 293.8 GB = 1.9%.**

### 5c. Ambiguous — review queue, do not touch (11 groups: 6 pipeline sets + 5 individual)

| Group | Why blocked |
|---|---|
| `AllSky - 220 Sky Skybox Set` | **v5.1.0 mtime 2024-11-29 > v5.2.0 mtime 2024-09-03.** Higher semver has older mtime — OneDrive rewrote timestamps. mtime is not a version signal. Both 5948.4 MB. If truly identical, +5.95 GB recovery, but content-hash requires hydrating 12 GB. |
| `Combat Magic Spells - Volume II` v1.0 | 110.3 vs 108.3 MB — same version, different bytes. Publisher re-upload without version bump. |
| `Non-Convex Mesh Collider…` v2.0 | 9.0 vs 9.4 MB — same as above. |
| `Retro Horror Template` | unversioned (newer mtime) vs v3.1, identical size. Could be v3.1 re-download or v3.2. |
| `GUI - Simple Round` v1.2.2 | `.zip` **and** `.unitypackage` — delivery formats, not versions. Zip likely holds editable PSD sources. Keep both. |
| 6 × render-pipeline sets | `ChineseAlley`, `ModernCity`, `Stylized Azure Hillside`, `Stylized Solarpunk City`, `Stylized Lowpoly Cyberpunk City`, `Stylized Paradise Gardens` — `{URP, HDRP, Builtin}` triples/pairs, ~17 files. **Never prune.** |

Also excluded from any suffix-stripping: `Beautify HDRP`, `Volumetric Lights 2 HDRP` — separate store SKUs, "HDRP" is part of the product name.

### 5d. Integrity flags (surface in viewer, no auto-action)

`Particle Dynamic Magic 2 v2.5.3` = **4096 bytes** — broken download. Five more under 100 KB: `Prefab Brush`, `Favorites Tabs`, `Toolkit for Discord Social`, `Dissonance For FMOD Playback`, `FMOD_InstrumentalEventPlayback`, `2D ULTIMATE BUNDLE`, `Warrior Pack Mega Bundle`. Some are legitimately tiny (FMOD integration shims); flag, don't judge.

---

## 6. Architecture

**Principle: one durable data file, cheap regenerable views, every mutating step a reviewable dry-run.**

```
Game Assets/Unity/
├── index.html                  # self-contained viewer, JSON inlined (CORS blocks file:// fetch)
├── .index/
│   ├── assets.json             # SOURCE OF TRUTH
│   ├── overrides.json          # hand-fixes keyed by asset id — never overwritten
│   ├── cache/{id}.json         # raw store responses, permanent → re-runs free
│   ├── thumbs/{id}.jpg         # mirrored; Unity CDN key-image URLs are versioned and rot
│   ├── reorg-manifest.json     # old→new path map = reorg undo
│   ├── quarantine-manifest.json
│   └── review-queue.md         # unresolved + ambiguous dups
├── _Quarantine/OldVersions/
├── _Unresolved/                # created by Phase 4 only
└── tools/unity-asset-index.py
```

### Pipeline

```
0 SCAN     walk + stat only. NEVER open an archive.
1 PARSE    title · version · release date · pipeline · flags · folder hint
2 RESOLVE  web search name → /packages/<cat>/<sub>[/<sub2>]/<slug>-<id>
           ├─ name fuzzy-match below threshold → review-queue.md, stop
           └─ pass → continue
3 FETCH    JSON-LD (author, thumb, breadcrumb, canonical URL, rating, price)
           + legacy API (cross-check publisher/version). Cache to disk. Rate-limit 1-2 req/s.
4 TAG      closed vocabulary only
5 MERGE    overrides.json over generated data → assets.json
6 EMIT     index.html + assets.csv
--- gate: user reviews dedupe plan ---
7 QUARANTINE  [!! CUT — NOT BUILT !!] mv safe dups → _Quarantine/OldVersions/ + manifest
--- gate: user reviews reorg plan ---
8 REORG       [!! CUT — NOT BUILT !!] hydration probe → mv into store tree + undo manifest
```

> **Steps 7 and 8 above were cut and are NOT implemented.** Quarantining dataless
> placeholders frees zero bytes, and 92.5% of the library is already in correct store
> level-1 folders. The shipped pipeline ends at step 6. Duplicates are *flagged* in
> `review-queue.md` for manual action — nothing is ever moved or deleted.
> See [plan.md](./plan.md) → "Explicitly out of scope".

Step 8 loops to 0 so paths regenerate post-move. Ordering is deliberate: **the index is the review tool for the destructive steps.** Approving an 861-file reorg without a filterable view of the library first is not reviewable.

### Data model

```json
{
  "id": "68570",
  "name": "Amplify Shader Editor",
  "author": "Amplify Creations",
  "category": { "path": "Tools/Visual Scripting", "levels": ["Tools", "Visual Scripting"] },
  "tags": ["shader", "editor-tool", "visual-scripting"],
  "thumbnail": { "remote": "https://…/key-image/….png", "local": ".index/thumbs/68570.jpg" },
  "store": "https://assetstore.unity.com/packages/tools/visual-scripting/amplify-shader-editor-68570",
  "rating": 5.0, "reviews": 701, "price": "80.00",
  "versions": [
    { "version": "1.9.9.12", "file": "Tools/Visual Scripting/Amplify Shader Editor v1.9.9.12.unitypackage",
      "sizeBytes": 12345678, "releaseDate": "2025-06-01", "latest": true,
      "pipeline": null, "quarantined": false }
  ],
  "resolution": { "method": "web-search", "confidence": 0.94, "verifiedName": true },
  "flags": []
}
```

`resolution.confidence` is load-bearing — the only guard against the silent-wrong-package failure in §3.

### Version ordering rule

Sort `versions[]` by **parsed semver, descending. Never by mtime** — proven wrong by AllSky (§5c). Unparseable version → `latest: null`, route to review queue.

### Tag vocabulary — closed, ~50 entries

Free-form LLM tagging on 861 assets yields ~400 near-synonyms (`shader`/`shaders`/`shading`) and destroys the facet. Derive deterministically from store category + keyword dictionary; LLM fills gaps only, constrained to the fixed list. Seed: `shader terrain character animation vfx sfx music ambient ui ai-pathfinding networking editor-tool optimization template kit low-poly stylized pbr realistic urp hdrp built-in horror medieval sci-fi fantasy modern vehicle weapon prop environment skybox particle post-processing lighting physics dialogue inventory procedural mocap 2d 3d gui icons fonts textures`. Persist `tagSource` per tag so manual tags survive regeneration.

---

## 7. Phasing

| Phase | Delivers | Risk | Reversible |
|---|---|---|---|
| 1 | Offline index — name, version, date, size, path, folder category, keyword tags, dup groups, integrity flags → working `index.html` | none, read-only | n/a |
| 2 | Store enrichment — author, real category, thumbnail, store link, rating; `review-queue.md` | none, read-only | n/a |
| 3 | Quarantine 9 semver pairs + 7 identical copies = 17 moves (~5.5 GB) | low | yes, `mv` back via manifest |
| 4 | Reorg into store tree, publishers dissolved, `_Unresolved/` bucket | **highest** | yes, undo manifest |

Each phase independently useful. Ship 1 before starting 2.

---

## 8. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Reading archives hydrates 294 GB | critical | walk+stat only; hard rule, enforce in code review |
| OneDrive move hydrates in Phase 4 | critical | single-file probe + `blocks==0` assert before batch; abort → skip Phase 4 |
| Wrong package resolved silently (HTTP 200, different asset) | high | name fuzzy-match + confidence score + ⚠ badge + review queue |
| Pruning deletes a pipeline variant | high | pipeline-variant sets hard-excluded from prune; quarantine not delete |
| mtime-based "latest" picks wrong version | high | semver-only ordering; AllSky is the regression test |
| Reorg destroys publisher curation (Synty, Meshtint, Kevin Iglesias, Mocap Online, RamsterZ, FImpossible) | medium | accepted per decision 5; viewer author filter replaces it; undo manifest available |
| Store scraping breaks (unofficial, no contract) | medium | permanent response cache → breakage never loses acquired data; rate-limit politely |
| CDN thumbnail URLs rot (`?v=1` versioned) | low | mirror to `.index/thumbs/` |
| ~90-130 items unresolvable | medium | `overrides.json` + `_Unresolved/`; do not pretend automation is complete |
| `file://` links can't "reveal in Finder" | low | show path + copy-to-clipboard button alongside the link |
| Regeneration clobbers hand-fixes | medium | overrides merged last, keyed by stable id |

---

## 9. Success metrics

- 861/861 archives appear in `index.html`.
- ≥85% resolved to a store package with `confidence ≥ threshold` and verified name.
- 0 items with author/thumbnail/link written from an unverified match.
- Search+filter response feels instant (861 rows — trivially met client-side).
- Phase 3 recovers ~5.5 GB with 0 pipeline variants and 0 `.zip`/`.unitypackage` pairs touched.
- Phase 4 completes with `blocks==0` preserved on all moved files, and undo manifest round-trips.
- Re-run after adding N new packages: only N new network lookups; all overrides intact.

---

## 10. Rejected approaches

| Approach | Why rejected |
|---|---|
| Markdown `INDEX.md` table | GFM tables cannot filter or sort; 861 inline remote images render badly. Fails the stated requirement. Keep as optional export. |
| CSV/XLSX only | Good filtering for near-zero effort, but multi-value tags filter clumsily in one cell and `file://` handling is awkward. Retained as a **secondary generated export**, not the primary. |
| Obsidian vault + Dataview/Bases | Genuinely strong for long-term library management with per-asset notes. Rejected only because it requires Obsidian; revisit if per-asset notes become a need. |
| SQLite + Datasette | Overkill at 861 rows. YAGNI. |
| Notion / Airtable | Manual sync, uploads purchase data to cloud, hits free-tier row limits. |
| Authenticated purchases API | Most accurate and cheapest (~9 requests, exact library scope). Rejected per decision 2 — token acquisition friction. **Reconsider if web-search resolution lands below ~85%.** |
| Reading `.unitypackage` contents for metadata | Contains no store metadata (tar of GUID dirs) *and* would hydrate 294 GB. |
| Sitemap-driven bulk catalog | Sitemap lists publishers only, no package URLs. |

---

## 11. Open questions

1. **Fuzzy-match threshold** — needs empirical calibration on a 30-asset sample before committing. Too strict inflates the review queue; too loose reinvites the wrong-package failure.
2. **AllSky duplicate** — resolving whether the two 5948.4 MB files are byte-identical requires hydrating ~12 GB. Worth it for 5.95 GB recovery? Deferred to user.
3. **`Combat Magic Spells` / `Non-Convex Mesh Collider`** — same version, different bytes. Keep larger, keep newer-mtime, or keep both? No principled automatic answer.
4. **Phase 4 hydration behavior** — unverified until the single-file probe runs. Phase 4 is contingent on it.
5. **Non-store archives** (`Kenney Game Assets All-in-1.zip`, `BattleSimulator.zip`, `SrRubfish_VFX_02`, `WM_Animset`) — index with hand-written metadata, or exclude from the store-category tree entirely?
