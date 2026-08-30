---
phase: 1
title: "Phase 1: Offline Index and Viewer"
status: todo
priority: P1
effort: "1d"
dependencies: []
---

# Phase 1: Offline Index and Viewer

## Overview

Ship a working searchable index with zero network calls and zero mutation. Scan the tree without opening a file, parse filenames, group duplicates safely, and emit a self-contained `index.html` with live search and facet filters.

Usable on its own. Category and tags at this stage are folder- and keyword-derived, marked `source: "folder"` so Phase 2 can overwrite them unambiguously.

## Requirements

**Functional**
- Walk the tree collecting `rel_path`, `st_size`, `mtime`, `st_blocks`, `format` for all `.unitypackage` / `.zip`
- Parse title, version, release date, pipeline variant, discriminators, flags — from `rel_path`, not just the basename
- Assign a stable `asset_key` per asset, persisted, path-independent
- Set `non_store: true` on archives that cannot originate from the Asset Store, so Phase 2 skips them entirely
- Persist `folder_hint` whenever the parent folder supplied title or version
- Group duplicates by size-collision, discriminated by title/version; **flag only, never move**
- Emit `assets.json`, self-contained `index.html`, `assets.csv`
- Viewer: live text search; facets for category tree, tags, author, duplicates, integrity; sort by name/size/date; grid + list; copy-path button; `file://` link

**Non-functional**
- **Never open an archive.** `os.walk` + `os.stat` only
- Scan must report exactly 861 — no name-based directory skipping
- `index.html` works from `file://` with no server and no external assets
- `assets.json` written atomically (tmp → `fsync` → `os.replace`)

## Architecture

Single module `.index/bin/index_assets.py` (~350 lines), functions grouped by concern. Split further only if a real second consumer appears — the predecessor's 21-module split had 9 single-function files and no shared boundaries.

```
scan(root)              -> [{rel_path, size, mtime, st_blocks, format}]
parse(rel_path)         -> {title, version, release_date, pipeline, discriminators, flags}
group(entries)          -> [{members, verdict, reason}]     # size-first, title-discriminated
build(scanned)          -> [AssetEntry]  with asset_key + versions[] newest-first by semver
emit_html(entries)      -> index.html    # JSON inlined; textContent rendering; CSP meta
emit_csv(entries)       -> assets.csv
```

### Exclusion is path-based, never name-based

`tools` and `Tools` share inode 1000698 here. The predecessor's `Skip … tools/` would have silently dropped 191 archives — 22% of the library — and all three red-team reviewers found it independently. Exclude by resolved absolute path (`os.path.realpath`) against an explicit list: `.index/`, `plans/`, `.claude/`, `_Quarantine/`. Code lives in `.index/bin/`, already excluded and outside the asset tree.

### Duplicate grouping: size-collision first, title as discriminator

Measured on this library:
- Title-normalization alone found 5.49 GB and **missed** `66 Creatures` (4.16 GB) — 11 size-collision groups total **15.21 GB**
- Size-collision alone **over**-groups: `Particle Dynamic Magic 2` and `Prefab Brush v1.3.3` are both exactly 4096 bytes, two unrelated broken downloads

So: size-collision generates candidates, title/version/discriminators confirm. Verdict is `duplicate` / `variant` / `distinct` — **never an action**. Nothing moves in this plan.

## Related Code Files

- Create: `.index/bin/index_assets.py`
- Create: `.index/bin/tests/test_index_assets.py`
- Generates: `.index/assets.json`, `index.html`, `assets.csv`, `.index/review-queue.md`

## Implementation Steps

### 1. Write the no-open test and the 861 test first

```python
def test_scanner_never_opens_a_file(self):
    """845/852 archives are OneDrive placeholders. Opening one hydrates it."""
    def explode(*a, **k):
        raise AssertionError("scanner opened a file — would hydrate OneDrive")
    with mock.patch("builtins.open", explode), mock.patch("io.open", explode), \
         mock.patch("tarfile.open", explode), mock.patch("zipfile.ZipFile", explode), \
         mock.patch("gzip.open", explode), mock.patch.object(Path, "read_bytes", explode):
        result = scan(self.tree)
    self.assertEqual(len(result), 2)

def test_scan_of_real_root_finds_861(self):
    """Guards the tools/Tools inode trap: 'tools' resolves to Tools/, which holds 191 archives."""
    self.assertEqual(len(scan(REAL_ROOT)), 861)
```

### 2. Implement `scan` until green

### 3. Write failing parse tests — real filenames, hazards measured on this library

Signature is `parse(rel_path)`, **not** `parse(filename)`: 15 files carry their version only in the parent folder (`3D/Chinese Alley Environment v1.0/ChineseAlley_Builtin_2021.3.6f1.unitypackage`), and two carry their title there too. The predecessor's basename-only signature structurally could not read them.

| Input | Expected |
|---|---|
| `Amplify Shader Editor v1.9.9.12.unitypackage` | version `1.9.9.12` |
| `Bakery - GPU Lightmapper vv1.96.unitypackage` | version `1.96` |
| `POLYGON City - Low Poly 3D Art by Synty 1.11.3.unitypackage` | version `1.11.3` — **no `v` prefix** |
| `Pure Nature 2 Meadows v2.1 (01 Apr 2025).unitypackage` | title `Pure Nature 2 Meadows`, version `2.1` — **the `2` is title, not version** |
| `All In 1 Sprite Shader v4.25.unitypackage` | title `All In 1 Sprite Shader` — the `1` is title |
| `Monsters Ultimate Pack 03 Cute Series v1.0.unitypackage` | title retains `03` |
| `Particle Dynamic Magic 2 v2.5.3 Unity.unitypackage` | version `2.5.3`, trailing `Unity` stripped |
| `POLYGON_NatureBiomes_MeadowForest_Unity_2021_3_v1_9_2.unitypackage` | version `1.9.2`, editor `2021.3` |
| `Horse Animset Pro (Riding System) v4.5.0-pre (30 Jul 2025).unitypackage` | version `4.5.0-pre`, **prerelease `True`** |
| `Procedural Generation Grid (Beta) v1.6.6.2 (03 Dec 2024).unitypackage` | version `1.6.6.2`, discriminator `Beta` |
| `Stylized Christmas Town (Unity 2020.3.26f1).unitypackage` | version **`None`** — editor version is never a package version |
| `ChineseAlley_URP_2021.3.6f1.unitypackage` in `Chinese Alley Environment v1.0/` | pipeline `URP`, version `1.0` from folder, `folder_hint` recorded |
| `Beautify HDRP v7.1.1 (24 Dec 2024).unitypackage` | pipeline `None` — `HDRP` is the product name |
| `GUI Pro - Simple Casual (PSD) v1.0.7.unitypackage` | discriminator `PSD` — **retained in the identity key**, so it can never group with the plain sibling |
| `Universal Fighting Engine 2 (Source) v2.50.unitypackage` | discriminator `Source`, retained |
| `GSpawn - Level Designer (PRO) v3.4.0.unitypackage` | discriminator `PRO`, retained |
| `(SE) Bark Howl Growl v2.0.unitypackage` | leading paren is **part of the title** |
| `(Bug) Combat animations - Kung fu V1 v1.0.unitypackage` | leading paren is an **annotation**, stripped |
| `Kenney Game Assets All-in-1.zip` | format `zip`, version `None` |

**Version disambiguation rule, stated explicitly** (the predecessor left this undefined, which is what generated 34 false groups): a `v`-prefixed dotted token wins; else the **last** dotted token with ≥2 components; never a token inside `(Unity …)`; never a token in title position followed by more title words.

### 4. Implement `parse` until green

### 5. Write failing version-ordering tests

```python
# Horse Animset Pro: a pre-release must never outrank a stable sibling
self.assertEqual(pick_latest([("4.5.0-pre",), ("4.4.8b",), ("4.4.7",)])[0], "4.4.8b")
# AllSky: semver only. mtime is scrambled by OneDrive.
self.assertEqual(pick_latest([("5.1.0", NEWER_MTIME), ("5.2.0", OLDER_MTIME)])[0], "5.2.0")
```

Plus `2.18.5 > 2.16.0`, `2.10 > 2.9`, `1.6.6.2`, `3.981`, `1.0.4.0`, `2.11.0a`, `1.1.1EA`, unparseable → `None` → review.

### 6. Write the false-grouping test — the safety invariant

```python
def test_no_duplicate_verdict_across_distinct_products(self):
    """34 false groups arise from naive leftmost-version parsing."""
    for family in [
        ["All In 1 3D-Shader v1.61", "All In 1 Springs Toolkit v1.45",
         "All In 1 Sprite Lighting v2.25", "All In 1 Sprite Shader v4.25",
         "All In 1 Vfx Toolkit v2.1"],
        ["Pure Nature 2 Meadows v2.1", "Pure Nature 2 Mountains v2.1",
         "Pure Nature 2 Islands v2.1", "Pure Nature v1.2"],
        ["Monsters Ultimate Pack 01 Cute Series v1.0",
         "Monsters Ultimate Pack 03 Cute Series v1.0"],
        ["Epic Toon VFX 2 v1.3", "Epic Toon VFX 3 v1.0"],
        ["GUI Pro - Simple Casual (PSD) v1.0.7", "GUI Pro - Simple Casual v1.0.7"],
    ]:
        for g in group([parse(f + ".unitypackage") for f in family]):
            self.assertNotEqual(g.verdict, "duplicate", f"false group: {family}")
```

The predecessor's invariant — "same `pipeline` and same `format`" — was **vacuously true** for all 34 of these, since they share both. The real invariant: no `duplicate` verdict when titles differ by any non-numeric token, or when discriminator sets differ.

### 7. Implement `group` and `build` until green

`asset_key` = stable slug of normalized title + discriminators + pipeline. Persisted in `assets.json`; reused on re-scan; defined for unresolved items.

### 8. Write failing escaping tests, then `emit_html`

Serialize with `<`, `>`, `&`, U+2028, U+2029 as `\uXXXX` — **not** substring replacement. The predecessor specified escaping the literal `</script>`, and its own test payload used exactly that form; `</ScRiPt >` and `</script/>` both inject (verified: 1 `<img>` parsed out of the data block each).

Parameterize over `</script>`, `</ScRiPt >`, `</script/>`, `<!--`, `javascript:`, U+2028 — across `title`, `author`, each breadcrumb, `store`, `thumbnail`, `tags[]`, and an override-supplied field. Render all data-derived strings via `textContent` / `setAttribute`, never string-concatenated HTML. Include `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'">`.

Also assert no external resource-loading attributes (`src="http`, `@import`, stylesheet `href="http`). Store links as `<a href>` are expected and allowed.

### 9. Implement `emit_csv` and `review-queue.md`

Review queue sections: duplicates (all 11 size-collision groups, identity marked `size+name, unhashed`), integrity suspects, unparseable versions, folder-hint-dependent files, non-store archives.

**Integrity thresholds are stated in bytes, never as "100 KB"** — the decimal/binary ambiguity already produced two wrong numbers in this project's history:

| Tier | Threshold | Count | Meaning |
|---|---|---|---|
| `broken` | `< 10_000 B` | 3 | Almost certainly a failed download (two are exactly 4096 B) |
| `suspicious` | `< 1_000_000 B` | 7 | Small for a Unity package; some are legitimate FMOD/integration shims |

**Same-folder duplicates** (`66 Creatures Super Mega`, both 4,162,660,084 B in `3D/`) get reason `same-size-same-folder` and **no preference heuristic** — neither path depth nor filename verbosity picks a winner. One entry, both files listed, decision left to the user.

**Non-store archives** (`Kenney Game Assets All-in-1.zip`, `BattleSimulator.zip`, `SrRubfish_VFX_02`, `WM_Animset/*`) are flagged `non_store: true` and listed separately. Phase 2 never attempts to resolve them, so they consume no lookups and never appear in the unresolved count.

### 10. Run against the real library

```bash
python3 .index/bin/index_assets.py scan emit
python3 -c "import json;d=json.load(open('.index/assets.json'));print(len(d['assets']),'assets',sum(len(a['versions']) for a in d['assets']),'files')"
open index.html
```

File count must be 861. Then confirm the materialized path-set is unchanged — a per-path diff including `.zip`, never a count:

```bash
find . -type f \( -name '*.unitypackage' -o -name '*.zip' \) -exec stat -f '%b|%N' {} + | awk -F'|' '$1>0{print $2}' | sort > /tmp/after.txt
diff /tmp/before.txt /tmp/after.txt && echo "no hydration"
```

## Success Criteria

- [x] Scan reports exactly **861**
- [x] No-open test green with all six patch points raising
- [x] No name-based directory skipping anywhere in the source
- [x] Materialized path-set identical before/after scan (per-path diff, `.zip` included)
- [x] `Horse Animset Pro` latest is `v4.4.8b`, not `v4.5.0-pre`; AllSky latest is `5.2.0`
- [x] Zero `duplicate` verdicts across all 5 false-group families
- [x] All 11 size-collision groups present in `review-queue.md` (15.21 GB), identity marked unverified
- [x] `66 Creatures` pair carries reason `same-size-same-folder` with no winner chosen
- [x] A `(PSD)` / `(Source)` / `(PRO)` archive never shares an `asset_key` with its plain sibling
- [x] Non-store archives flagged `non_store: true`; integrity tiers expressed as byte literals
- [x] `folder_hint` persisted for all 15 folder-dependent files
- [x] All escaping variants contained; CSP meta present; `textContent` rendering
- [x] `index.html` opens from `file://`; search, facets, sort, copy-path all work offline
- [x] `assets.json` written atomically; two runs on unchanged input produce equal content ignoring `mtime`/`st_blocks`

## Risk Assessment

| Risk | Mitigation |
|---|---|
| A later change opens an archive | No-open test patches six entry points |
| Directory-name exclusion reintroduced | Path-based exclusion + the 861 assertion |
| Version regex overfits and creates false groups | Explicit disambiguation rule + 5 false-family tests + `None` → review |
| Pre-release marked latest | `Horse Animset Pro` is the regression test |
| Injection via scraped strings (Phase 2 data) | `\uXXXX` serialization + `textContent` + CSP, all tested in Phase 1 before any scraping exists |
| `assets.json` truncated by a crash | tmp → `fsync` → `os.replace` |
| Determinism test flaky on a live sync root | Compare ignoring `mtime` and `st_blocks`; both drift externally |
