# Implementation Status — 2026-07-30

**Phase 1: COMPLETE.** **Phase 2: COMPLETE — 717/825 store-eligible enriched (86.9%).**

Tests: **130 passing** (`python3 -m unittest discover -s .index/bin/tests -t .index/bin`)

## What exists

```
index.html                      # 691 KB self-contained viewer, CSP, no external requests
assets.csv                      # 861 rows, store + on-disk names, store URL column
.index/assets.json              # source of truth, atomic writes
.index/cache.json               # 180 resolution records (permanent, resumable)
.index/thumbs/                  # 151 mirrored key images
.index/review-queue.md          # duplicates · integrity · resolution · override snippets
.index/search-worklist.json     # 825 store-eligible titles
.index/hydration-baseline.txt   # materialized-path baseline for the no-hydration check
.index/bin/index_assets.py      # scan · parse · group · build · emit          (~640 lines)
.index/bin/resolve_store.py     # search · verify · fetch · mirror · merge     (~700 lines)
.index/bin/tests/               # 122 tests + captured fixtures
```

## Numbers (measured, not estimated)

| | |
|---|---|
| Archives indexed | **861** files → **835** assets |
| Duplicate groups | 9 `duplicate` (**9.26 GB** reclaimable) + 1 `review` (AllSky) + 1 `distinct` |
| Integrity | 3 `broken` (<10,000 B), 4 `suspicious` (<100,000 B) |
| Store-enriched | **717 / 825 store-eligible = 86.9%** — all with author + store URL + thumbnail + category |
| Category depth | 485 assets at 2 levels, 198 at 3, 26 at 4 |
| Store renames caught | **220** (store name differs from the filename-derived name) |
| Unresolved | **108** — see breakdown below |
| Hydration | see caveat below |

## Resolution outcome

Searching ran as 5 waves of ~6 agents (30 titles each), banked incrementally via
`import-ids`. Verification held at **83-93% per batch**, matching the 30-asset pilot.

| Verified via | Count |
|---|---|
| legacy API (`api.assetstore.unity3d.com`) | 687 |
| store detail page (fallback, see below) | 30 |

### The 108 unresolved, by cause

| Cause | Count | Actionable? |
|---|---|---|
| Sibling/variant rejected by the gate | ~74 | Yes — the store name differs enough that the gate refused. Pick the right id via `import-ids` |
| No candidate found at all | 19 | Mostly not: Synty store-exclusives, Unreal-only assets, delisted listings, and non-product names like `FMOD InstrumentalEventPlayback` |
| Store name diverged (upstream rename) | 10 | Yes if you accept the new name — e.g. `City 3 - Low Poly 3D Models Pack` → *Coastal Living City* |
| Legacy API had no record | ~5 remaining | Recovered 30 of these via the detail-page fallback |

## Historical blocker: WebSearch is capped at 200 calls per session

Shared across the parent session AND all subagents. Wave 1 (5 agents x 30 titles, ~1.5
searches each) consumed the entire budget. Two agents reported it independently before it
was confirmed directly.

Effective throughput is **~150-180 assets per session** at the default. Raised to **2000**
via `env.CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION` in `~/.claude/settings.json` (backup:
`settings.json.bak-20260730-154838`), which applied to the running session with no restart
and allowed the job to finish in one pass.

Scraped fallbacks are not a substitute — all measured this session:
- Brave: 429 for 10 consecutive probes over 7.5 min, no recovery
- DuckDuckGo html + lite: 202 "anomaly" after ~2 requests
- Bing RSS: never blocks, but returns 0 results for real titles (3 titles x 3 query forms)
- ecosia, mojeek: 403 · startpage, marginalia, yandex, bing-html: 0 package URLs

## To resume

```bash
# 23 batch files of <=30 titles are pre-generated and exclude everything already resolved:
#   {scratchpad}/batches-remaining/batch-01.json ... batch-23.json
# Per session, launch ~5 agents (that is the 200-search budget), then:
python3 .index/bin/resolve_store.py import-ids --ids-file <ids-batch-NN.json>   # serialize these
python3 .index/bin/resolve_store.py enrich
python3 .index/bin/index_assets.py emit
```

Regenerate the remaining batches at any time from `.index/search-worklist.json` minus the
`status == "resolved"` keys in `.index/cache.json`. `import-ids` runs must be serialized —
each loads and rewrites the whole cache.

Agent prompts must state: **the package id is the LAST digit run in the slug**
(`fps-framework-2-0-278978` → `278978`), and must warn against guessing across sibling
families (`Pure Nature 2 Meadows`/`Mountains`, `Epic Toon VFX 2`/`3`, `Ultimate Pack 01`/`02`,
`GPU Instancer`/`Pro`).

## Bugs found during implementation

| Bug | Impact | Caught by |
|---|---|---|
| `PACKAGE_URL_RE` non-greedy — captured `2` from `fps-framework-2-0-278978`, `02` from `robots-ultimate-pack-02-cute-series-213777` | Wrong id for **every** package whose slug contains digits. Would have written another package's author/category/thumbnail | The fail-closed `offers.url` check added for red-team M3. Fixing it moved the pilot 66.7% → 90% |
| HTML entities not decoded from JSON-LD | Categories read `Textures &amp; Materials` | Visible in the first enriched output. Fixed at parse **and** merge, so the 151 cached records self-repaired without refetching |
| `merge()` labelled never-searched assets `web-search` | Review queue claimed **675** needed manual overrides when only 29 did | Reading the generated review queue |
| Pool returned on the first empty result | One weak engine (bing) poisoned 28/30 assets while DuckDuckGo had the answers | Spike at 3.3%; fixing it → 13.3% |
| Cooldown doubled from current value | A cooling transport is never retried, so escalation could only ever fire once | Its own unit test |
| stdout block-buffered through a pipe | A multi-hour job showed zero progress | Running it |

Four gate rules were also relaxed — trailing store versions (`Animancer Pro v8`), word-split
differences (`ModernCity` vs `Modern City Pack`), and differing marketing tails — each
verified against all seven sibling-SKU traps to confirm none of them opened.


### Bugs found in the enrichment run itself

| Bug | Impact | Caught by |
|---|---|---|
| `json.loads` strict mode rejected the Product block | The store embeds raw newlines in descriptions. Every such package silently lost its store URL, category and thumbnail despite a verified id | `Final IK` showing "no JSON-LD" while curl found 2 blocks |
| Invalid `\` escape in descriptions | Same effect; `strict=False` does not cover it. Needed an escape-repair pass | The last 4 stragglers after the strict fix |
| Verification required the legacy API | That endpoint has no record for a sizeable minority of ids. The store's own detail page is equally authoritative — requiring the legacy API rejected 30 assets the store could verify | Six spot-checks all showing `legacy=MISS, detail_name=<correct>` |
| `merge()` derived `local_name` from `asset["name"]` | Non-idempotent: `assets.json` is merge's own input, so a second `enrich` overwrote the real on-disk name with the store name. Corrupted 213 of 220 records | The store-renamed count collapsing 159 → 7 |
| Progress print indexed `rec['legacy']['publisher']` | `KeyError` mid-run once the detail-page fallback left `legacy` empty | Running the fallback retry |

Every one surfaced from reading real output or watching a number move the wrong way —
none from re-reading the code.

## Verified end-to-end

- **Thumbnails**: 151/151 present on disk, all 151 reachable from `index.html`, none over
  the 2 MB cap, **zero** attached to an unverified row. Extensions come from the sniffed
  content type (all `.jpg` here, but the code does not hardcode it).
- **Correction workflow**: `Easy Flying System` failed verification because the store lists
  it as plain "Flying System". Feeding the correct id through
  `import-ids --ids-file` resolved it to id 254446 / Woody's Games / Templates/Systems with
  a mirrored thumbnail — the documented repair path, exercised on real data rather than only
  in unit tests.
- **Store-name authority**: that entry now displays "Flying System" with
  `on disk: Easy Flying System` beneath, which is the intended behaviour for the 53 assets
  the store has renamed.

### Minor: thumbnails are keyed by store id, so a cache reset can orphan one

Mirrored images live at `.index/thumbs/{store_id}.jpg`. If a cache entry is discarded and the
asset later resolves to a *different* id, the old image is left behind. One orphan
(`254446.jpg`) appeared this way and disappeared once the entry was corrected. Harmless — a
few hundred KB — but a `--prune-thumbs` sweep comparing `.index/thumbs/` against the ids in
`assets.json` would tidy it.

## Hydration caveat — read this

One archive materialized during the session:
`VFX/Shaders/Amplify Impostors v1.0.0 (08 Apr 2025).unitypackage` (207 MB).

**Not caused by this tooling.** A full real-tree scan with `open`, `io.open`, `tarfile`,
`zipfile`, `gzip` and `Path.read_bytes` all patched to raise completes at 861 files without
opening anything. The file's `atime` (10:46) predates the baseline capture, and its `ctime`
(13:42) marks when OneDrive finished its own download.

The remaining 8 baseline paths are unchanged. Verify at any time:

```bash
find . -type f \( -name '*.unitypackage' -o -name '*.zip' \) -exec stat -f '%b|%N' {} + \
  | awk -F'|' '$1>0{print $2}' | sort > /tmp/now.txt
diff .index/hydration-baseline.txt /tmp/now.txt
```

A path-set diff is used rather than a count precisely because OneDrive rehydrates on its own
schedule — a count would have reported 8→9 and named nothing.

## Out of scope (cut during red-team, still cut)

- Moving, quarantining, or deleting any archive. Duplicates are **flagged only**.
- Reorganising the folder tree, including the 65 root strays.
- Opening any archive for any reason.
- Byte-identity verification of duplicates (needs ~10 GB hydration; recorded as
  `size+name, unhashed`).

## Unresolved questions

1. Raise `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION` to finish in one pass, or spread across
   ~5 sessions? A search API key or Unity Bearer token would remove the dependency on an
   interactive session entirely.
2. `66 Creatures Super Mega` — 4.16 GB size-identical pair in the **same** folder, so no path
   heuristic discriminates. Which copy is authoritative?
3. The 30 override candidates include genuine upstream renames (`Easy Flying System` →
   *Easy Drone Controller*, `AAR Action RPG SFX Pack` → *Action RPG SFX Pack v2*). Accept the
   store's current name, or keep the name as purchased?
4. `ChineseAlley` builtin/hdrp/urp all map to one store id (315413) — one package shipping
   three pipelines. Keep three entries, or collapse to one with three files?
