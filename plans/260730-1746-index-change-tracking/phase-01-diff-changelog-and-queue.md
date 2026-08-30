---
phase: 1
title: "Phase 1: Diff, Changelog and Queue"
status: todo
priority: P1
effort: "4h"
dependencies: []
---

# Phase 1: Diff, Changelog and Queue

## Overview

Add the `update` verb: rescan, diff against the previous state, classify what appeared, append a changelog entry, and queue genuinely-new assets. Pure logic first, then the CLI wiring.

Delivers a working command on its own — Phase 2 only makes its output more visible.

## Requirements

**Functional**
- Derive the previous state `{rel_path: size}` from the existing `assets.json`
- Diff against a fresh scan on `(path, size)`; classify `added` / `removed` / `resized`
- For each added path: known asset (cached resolution reusable) vs new asset (needs a search)
- Append a newest-first entry to `.index/CHANGES.md`
- Write `.index/pending-enrichment.json` with `asset_key`, title, `first_seen`
- `update` = scan + diff + changelog + queue + emit, in that order

**Non-functional**
- **Never open an archive.** `os.walk` + `os.stat` only, as with `scan`
- **`mtime` must not influence the diff** in any way
- Idempotent: a second `update` with no disk change appends nothing
- No previous state → establish a baseline, write no changelog entry
- `CHANGES.md` and the queue written atomically (`write_atomic`)

## Architecture

Four pure functions plus one I/O helper, all in `index_assets.py`:

```python
manifest_of(data)                 -> {rel_path: size_bytes}     # from an assets.json dict
diff_manifest(prev, now)          -> {"added":[], "removed":[], "resized":[]}
classify_added(paths, cache)      -> {"new_assets":[], "new_versions":[]}
format_changelog_entry(...)       -> str                        # newest-first block
append_changelog(path, entry)     -> None                       # prepend under header
```

`resized` entries carry `(path, old_size, new_size)` so the changelog can show the delta.

`classify_added` consults `.index/cache.json`:

| Cache state for the parsed `asset_key` | Classification | Needs a search? |
|---|---|---|
| present, `status == "resolved"` | `new_versions` | No — resolution reused |
| absent | `new_assets` | Yes |
| present, `status != "resolved"` | `new_assets`, flagged `previously_unresolved` | Yes, but a retry may not help |

That third row matters: the asset was already searched and failed, so the queue should say so rather than implying a fresh lookup will succeed.

### Changelog format

```markdown
# Change Log

Sizes are compared, not modification times — OneDrive rewrites mtimes, so an
mtime change means nothing here. A file mid-upload can briefly report a
different size and show up as `resized`.

## 2026-08-02 14:31 — +3 −1 ~0

**NEW ASSETS (2)** — need enrichment: `resolve_store.py export-titles --pending`
- `Foo Bar v1.0.unitypackage` → `foo-bar`
- `Baz Kit v2.unitypackage` → `baz-kit` _(previously unresolved)_

**NEW VERSION of a known asset (1)** — resolution reused, no search needed
- `DunGen v2.19.0.unitypackage` → `dungen` (had v2.18.5)

**REMOVED (1)**
- `Old Thing v1.0.unitypackage`
```

## Related Code Files

- Modify: `.index/bin/index_assets.py` — add the five functions and the `update` verb
- Modify: `.index/bin/tests/test_index_assets.py` — new test class
- Generates: `.index/CHANGES.md`, `.index/pending-enrichment.json`

## Implementation Steps

### 1. Write the four load-bearing tests first

```python
def test_mtime_only_change_yields_no_diff(self):
    """AllSky: identical size, higher version, OLDER mtime — OneDrive rewrites
    timestamps, so any mtime sensitivity invents phantom changes."""
    prev = {"A v1.0.unitypackage": 1000}
    now  = {"A v1.0.unitypackage": 1000}      # same size, mtime irrelevant by construction
    d = ia.diff_manifest(prev, now)
    self.assertEqual((d["added"], d["removed"], d["resized"]), ([], [], []))

def test_diff_signature_does_not_read_mtime(self):
    """Structural guard: mtime must not appear in the diff path at all."""
    src = Path(ia.__file__).read_text(encoding="utf-8")
    body = src.split("def diff_manifest(", 1)[1].split("\ndef ", 1)[0]
    self.assertNotIn("mtime", body)

def test_rename_to_same_asset_key_is_a_known_asset(self):
    """'Downloaded v2.19 over v2.18' — must not burn a search."""
    cache = {"dungen": {"status": "resolved", "id": "15682"}}
    got = ia.classify_added(["Tools/DunGen v2.19.0.unitypackage"], cache)
    self.assertEqual(got["new_assets"], [])
    self.assertEqual(len(got["new_versions"]), 1)

def test_update_twice_appends_one_entry(self):
    """Same idempotency class as the local_name bug that corrupted 213 records."""
    # run update twice on an unchanged temp tree; assert one '## ' header in CHANGES.md
```

### 2. Write the remaining diff tests

`added` only · `removed` only · `resized` reports old and new size · a rename appears as one added + one removed · an empty previous manifest yields no diff and no changelog (baseline case) · ordering is deterministic.

### 3. Implement `manifest_of` and `diff_manifest` until green

### 4. Write `classify_added` tests, then implement

Cover all three cache states from the table above. Include a real case: `DunGen v2.19.0` against a cache holding `dungen` resolved, and a genuinely new title absent from cache.

### 5. Write changelog tests, then implement

- Entry is prepended (newest first), header preserved, prior entries intact across three appends
- Counts in the `## <date> — +N −N ~N` line match the section contents
- An empty diff produces **no** entry rather than an empty one
- `write_atomic` used — no `.tmp` left behind

### 6. Extend the no-open test to cover `update`

The existing test patches `open`, `io.open`, `tarfile`, `zipfile`, `gzip`, `Path.read_bytes` around `scan`. Add a case doing the same around a full `update` on a temp tree. `update` writes text files, so patch `builtins.open` selectively — assert no *archive* path is opened rather than no file at all. Simplest form: assert `tarfile`/`zipfile`/`gzip` are never called and that no path ending in `.unitypackage`/`.zip` is ever passed to `open`.

### 7. Wire the `update` verb

```bash
python3 .index/bin/index_assets.py update
```

Prints a one-line summary (`update: +3 −1 ~0, 2 new assets queued`) then the usual `emit` line. Line-buffer stdout as `resolve_store.py` does.

### 8. Run against the real library

```bash
python3 .index/bin/index_assets.py update      # expect +0 −0 ~0, no changelog entry
python3 .index/bin/index_assets.py update      # expect identical — idempotent
```

Then a real round-trip: copy any small archive to a scratch name inside the tree, `update`, confirm it appears as an added new asset and lands in the queue, delete it, `update`, confirm the removal is logged. Use a **0-byte placeholder file** named `*.unitypackage`, not a copy of a real archive — copying a real one would hydrate it.

Finally confirm the materialized path-set is unchanged.

## Success Criteria

- [x] All four load-bearing tests green
- [x] `mtime` appears nowhere in `diff_manifest`
- [x] `update` on an unchanged tree: `+0 −0 ~0`, no changelog entry, run twice identical
- [x] Added / removed / resized each reported correctly against the real tree
- [x] Rename to a same-`asset_key` name → `new_versions`, not queued
- [x] Previously-unresolved asset re-appearing is queued **and** flagged as such
- [x] Missing `assets.json` → baseline message, no changelog entry
- [x] No archive opened; materialized path-set unchanged
- [x] `CHANGES.md` and the queue written atomically, no stray `.tmp`
- [x] Full suite green

## Risk Assessment

| Risk | Mitigation |
|---|---|
| `mtime` creeps back into the diff | Structural test greps `diff_manifest` for the string; behavioural test asserts zero entries |
| A file mid-upload reports a changing size → spurious `resized` | Documented in the `CHANGES.md` header. Placeholders report full logical size so this is unlikely, but it is not impossible and should not be hidden |
| Second `update` duplicates the entry | Idempotency test; empty diff writes nothing |
| Missing `assets.json` reported as 861 additions | Explicit baseline guard, tested |
| Testing the round-trip hydrates an archive | Use a 0-byte placeholder file, never a copy of a real archive |
| `update` accidentally opens an archive | Extended no-open test |
| Changelog grows unbounded | Accepted (open question 1). At this rate it is years before it matters |
