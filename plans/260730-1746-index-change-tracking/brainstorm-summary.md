# Brainstorm — Index Change Tracking

**Date:** 2026-07-30
**Status:** design approved, not implemented
**Follows:** `plans/260730-1154-unity-asset-index/` (Phase 1+2 complete, 717/825 enriched)

---

## Q1 — "861 files → 835 assets, how about the remaining?"

**Answered, no work needed. Nothing is missing.**

24 assets group 50 files, collapsing 26. Distribution: 811 assets hold 1 file, 22 hold 2, 2 hold 3 → `22×1 + 2×2 = 26`.

| Reason | Assets | Example |
|---|---|---|
| Multiple versions kept | 11 | `Horse Animset Pro` ×3, `Magic Animation Blend` ×3, `COZY Stylized Weather 3` ×2 |
| Cross-folder duplicate, same version | 7 | `Animation Preview Pro` in both `3D/Animations/` and `Tools/` |
| Same version, multiple files | 3 | `Combat Magic Spells - Volume II` (110.3 vs 108.3 MB) |
| Same-folder duplicate | 2 | `66 Creatures`, `Retro Horror Template` |
| Same size, different version | 1 | `AllSky` 5.1.0 / 5.2.0 |

Every one of the 861 files is indexed: `assets.csv` has 861 rows (one per file), viewer header reads "835 assets · 861 files". Direct consequence of the approved "one entry per asset, `versions[]` nested" model.

---

## Q2 — change tracking + automatic index update

### Requirements (locked)

| # | Decision |
|---|---|
| 1 | **Trigger:** manual command only. No scheduler. |
| 2 | **History:** append-only `.index/CHANGES.md`. No git. |
| 3 | **New assets:** queue to `.index/pending-enrichment.json` + flag. Never auto-enrich. |
| 4 | **Devices:** single machine writes `.index/`. No locking needed. |

**Expected output:** one new CLI verb `index_assets.py update` producing an updated index plus a changelog entry plus a pending queue.
**Acceptance:** adding/removing/replacing an archive then running `update` yields a correct changelog entry, correct pending-queue membership, and a regenerated viewer — with zero archives opened.
**Out of scope:** scheduler, git, lock file, notifications, auto-enrichment, byte-level change detection.
**Touchpoints:** `.index/bin/index_assets.py`, `.index/bin/resolve_store.py`, `.index/bin/tests/`.

### Scout findings that shaped the design

- **Rescan = 0.09 s** for 861 files. Dissolves the whole "how do we efficiently detect changes" question — checking is cheaper than deciding whether to check.
- **Git cannot track "changes without content"** — a blob *is* the content. But a path+size manifest is only **62 KB** for all 861 files, so git-of-a-manifest was viable. Declined (decision 2).
- **`.git` inside this folder is a genuine hazard.** OneDrive would sync `.git/index`, `objects/`, `refs/`; a sync landing mid-write or a second device running git yields conflict copies of `.git/index` and a corrupted repo. macOS OneDrive has no per-subfolder exclusion (no Dropbox-style `.nosync`), so `.git` cannot be kept out of sync in place. **If git is ever wanted: repo lives outside the folder.**
- **`mtime` is untrustworthy here.** AllSky: identical size, higher version, *older* mtime, because OneDrive rewrites timestamps. Diff must key on `(path, size)`.
- **Detection automates; enrichment does not.** New *version* of a known asset reuses its cached resolution (cache is keyed by `asset_key`) — fully offline. Genuinely *new* asset has no cache entry → needs a web search → needs a session.
- Available: `git`, `launchd`. Absent: `fswatch`, `watchman`, `entr`.

### Design

```
python3 .index/bin/index_assets.py update
```

```
read existing assets.json  ->  previous state = {rel_path: size_bytes}
scan disk (0.09s, os.walk+os.stat only, never opens a file)
diff on (path, size)   [mtime deliberately ignored]
   added   -> parse -> asset_key
                 in cache as resolved?  yes -> new VERSION, resolution reused, automatic
                                        no  -> NEW asset -> pending-enrichment.json
   removed -> drop version; cache entry retained so a re-download resolves instantly
   resized -> publisher replaced in place without a version bump; report only
append .index/CHANGES.md  (newest first)
emit index.html + assets.csv + review-queue.md
```

**No separate manifest artifact.** `assets.json` already carries `(rel_path, size_bytes)` per file, so it *is* the previous-state record — read before overwrite. One less file to keep consistent (DRY).

**Rename falls out correctly** as removed + added; if the new filename normalizes to the same `asset_key` it is recognised as a known asset needing no search. Covers the common "downloaded v2.19 over v2.18" case.

### New artifacts

| Path | Contents |
|---|---|
| `.index/CHANGES.md` | Append-only, newest first: `## <date> — +N −N ~N`, then NEW ASSETS / NEW VERSION / REMOVED / RESIZED sections |
| `.index/pending-enrichment.json` | `asset_key`, title, `first_seen` for assets awaiting a search |

### Modified

- `index_assets.py` — `+ diff_manifest()`, `+ classify_added()`, `+ append_changelog()`, `+ update` verb; viewer gains a `generated` stamp in the header and a `pending-enrichment` filter chip.
- `resolve_store.py` — `export-titles --pending` emits a worklist of only the queued assets.

**Size:** ~150 lines + tests, no new modules.

### Why manual-only needs a staleness stamp

Manual trigger means the index goes stale silently and invisibly. Mitigation is one line: surface `data.generated` in the viewer header so opening `index.html` states when it was last scanned. Without it, a stale index is indistinguishable from a current one — the failure mode of the chosen trigger.

## Approaches considered and rejected

| Approach | Why rejected |
|---|---|
| launchd timer (30 min) | Sound and near-free at 0.09 s, but user chose manual. Remains the easy upgrade — one plist. |
| launchd WatchPaths / fswatch | Event-watching a File Provider mount is where the flakiness lives (missed events, sync storms) and buys little over polling a 0.09 s operation. |
| `git init` inside the folder | **Unsafe.** OneDrive syncs `.git/`; corruption and conflict copies of `.git/index`. No per-subfolder exclusion on macOS OneDrive. |
| External git repo + manifest | Technically clean, real `git diff`/blame. Declined: splits index artifacts from the assets they describe, and the changelog already answers "what changed". Offered as a later add-on. |
| Dated manifest snapshots (23 MB/yr) | Diffable but less useful than a changelog — requires diffing two files to answer "what changed last week". |
| SQLite + history table | Over-engineered for 861 rows. Rejected in the original brainstorm; unchanged. |
| Content hashing to detect change | Would hydrate up to 294 GB. Never viable. `(path, size)` is the only safe signal. |

## Risks

| Risk | Mitigation |
|---|---|
| Index silently stale (consequence of manual trigger) | `generated` stamp in viewer header |
| File mid-upload reports a growing size → spurious `resized` | Unlikely (placeholders report full logical size); note the possibility in the changelog header rather than pretend it cannot happen |
| `assets.json` deleted → all 861 look "added" | No-basis guard: print "no previous state, establishing baseline", write no changelog entry |
| A future change reintroduces mtime into the diff | Test asserts an mtime-only change produces zero diff entries |
| A future change opens an archive during diff | Existing no-open test covers `scan`; extend it to the `update` path |
| Second machine writes `.index/` despite decision 4 | Documented assumption; no code guard (accepted) |

## Success criteria

- Add an archive → `update` → changelog lists it, queue contains its `asset_key`, viewer shows it
- Add a *new version* of a known asset → changelog says "resolution reused", queue does NOT contain it
- Delete an archive → changelog lists removal, cache entry survives
- Replace an archive in place with different bytes → reported as `resized`, not re-resolved
- Rename to a name that normalizes identically → recognised as known asset, no queue entry
- An mtime-only change produces **zero** diff entries
- `update` opens zero archives; materialized path-set unchanged
- Running `update` twice in a row → second run writes no changelog entry
- Full suite green (130 tests today)

## Unresolved questions

1. Changelog retention — unbounded, or trim past N entries / N months? Unbounded is fine for years at this rate; worth deciding before it is 500 entries long.
2. Should `resized` invalidate anything? Current design reports only. If a publisher re-uploads under the same filename with real content changes, the recorded version string becomes wrong — but nothing detectable without hashing (which hydrates). Accepting "report only" unless you want a manual-review flag.
3. `pending-enrichment.json` growth if new assets are added but never enriched — should entries expire or just accumulate with `first_seen`? Accumulating is honest; expiry risks silently forgetting.
