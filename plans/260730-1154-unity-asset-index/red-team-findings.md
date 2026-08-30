# Red Team Review — 2026-07-30

Adversarial review of plan `260730-1105-unity-asset-index-and-reorg` (now superseded). Four hostile reviewers, Full verification tier, read-only. Every finding below was **independently re-verified** against the live 861-file tree before acceptance.

**Outcome:** the plan was withdrawn and rewritten as `260730-1154-unity-asset-index`. Four of its foundations were each independently wrong, so patching was rejected in favour of rebuilding smaller.

## Reviewers

| Lens | Verification role | Findings |
|---|---|---|
| Scope & Complexity Critic | Contract Verifier | 9 |
| Failure Mode Analyst | Flow Tracer | 10 |
| Security Adversary | Fact Checker | 10 |
| Assumption Destroyer | Scope Auditor | 10 |

## Accepted — Critical

| # | Finding | Verified evidence | Applied |
|---|---|---|---|
| 1 | `tools/` in the scanner skip-list **is** `Tools/` — 191 archives (22%) silently excluded, and tool source would land inside the asset tree | `stat -f %i tools Tools` → both `1000698`; `find Tools -type f` → 191. Found independently by 3 of 4 reviewers | Code moved to `.index/bin/`; exclusion is path-based; a test asserts scan = 861 |
| 2 | Quarantine recovers **zero bytes** — all 17 files are dataless placeholders, and moving inside one OneDrive root changes neither local disk nor cloud quota | All 17 `st_blocks=0`. The predecessor's own risk table said "total remains unchanged" three lines below a "~5.5 GB" headline | Quarantine phase **cut**. Duplicates flagged for manual deletion |
| 3 | Duplicate census 2.8× incomplete | Size-collision census: **11 groups, 15.21 GB of collisions** vs the plan's 5.49 GB. `66 Creatures Super Mega` (4.16 GB, size-identical) triaged nowhere. Implementation later split this honestly: **9.26 GB true duplicates**, 5.95 GB `review`, 4 KB `distinct` | Size-collision is the candidate generator; title/version discriminates |
| 4 | Naive version parsing creates **34 false duplicate groups** (~40 distinct paid products), and the protective invariant was **vacuously true** for all of them | `all in` ×5 (five unrelated tools), `pure nature` ×7, `monsters ultimate pack` ×7, `epic toon vfx` ×3. Invariant tested `pipeline` + `format`, which all members share | Explicit version-disambiguation rule + 5 false-family regression tests. Invariant rewritten: no duplicate verdict when titles differ by a non-numeric token |
| 5 | **The search transport is dead.** "Verified working" rested on one cold `curl` | DuckDuckGo HTML + lite: HTTP 202 anomaly, 0 package URLs, 4/4 requests. Brave works but 429s at request 5 | Transport re-selected (Brave), typed `Blocked` outcome, backoff, resume |
| 6 | `?locale=` URLs pass the name gate at **1.0** while returning localized categories; `Accept-Language` does not override it | `Product.name = 'Amplify Shader Editor'`, crumbs `['Home','工具','可视化脚本']` even with `Accept-Language: en-US` | Query string stripped before fetch; non-Latin breadcrumb is a hard reject |
| 7 | No similarity threshold separates true from false matches; the prescribed token-subset bonus fires on **66 distinct-product pairs** (reviewer said 61; my own measurement is 66) | `Epic Toon VFX 2`↔`3` = 0.933, above a real Synty rename at 0.872. `GPU Instancer` ⊂ `GPU Instancer Pro` | Verification by **numeric id** via a second endpoint. Similarity ranks candidates only; it never authorizes a write |
| 8 | The user-approval gate was not binding — `--execute` re-derived the move set from `assets.json` | `grep -rniE 'atomic\|fsync\|lock\|checkpoint'` → 0 matches across all 6 plan files | Moot: no phase mutates. `assets.json` now written atomically |
| 9 | `shutil.move` has an unguarded copy fallback into a File Provider domain; "same volume ⇒ rename" is the wrong causal model | `com.microsoft.OneDrive-mac.FileProvider` active; single `st_dev` is true but irrelevant to reparent semantics | Moot: no file moves |
| 10 | The hydration probe was assigned to Phase 4 by the brainstorm but existed **only** in Phase 5 | `grep -c probe` → 0 in phase-04, 10 in phase-05; brainstorm line 48 says "**Phase 4 gate**" | Moot: no moves. Read-only scan verified by per-path diff |

## Accepted — High

| # | Finding | Verified evidence | Applied |
|---|---|---|---|
| 11 | Hydration gate was a hardcoded count, already wrong, and excluded all 9 `.zip` | Actual 7 materialized (not 6), drifting daily; `POLYGON_Prototype_SourceFiles_v4.zip` materialized and invisible to every gate | Per-path set diff including `.zip`; never a count |
| 12 | Script-data escaping spec defeated by case and slash variants; scope covered only `title` | `</ScRiPt >` and `</script/>` each parsed 1 `<img>` out of the data block. The plan's own test used the one form its escape caught | `\uXXXX` serialization, `textContent` rendering, CSP meta, parameterized over 6 payloads × 5 fields |
| 13 | Pre-release parsed as final becomes "latest" | `Horse Animset Pro v4.5.0-pre` (340 MB) would outrank stable `v4.4.8b` (313 MB) and `v4.4.7` (301 MB) | Explicit prerelease handling + regression test |
| 14 | `overrides.json` keyed by store `id` cannot address unresolved items — which are exactly the ones needing hand-fixing | The plan told users to paste an override for `unresolved` entries, which have no id. Circular | Overrides keyed by `asset_key`: path-independent and defined for unresolved items |
| 15 | Two archives can legitimately resolve to one store `id` | 4 Synty naming-era pairs (`POLYGON_NatureBiomes_MeadowForest…` / `POLYGON Meadow Forest - Nature Biomes…`) | **Detected and reported, NOT auto-merged.** `merge()` records `shared_store_ids` and emits a warning; the two entries stay separate. Auto-merging would need a rule for combining versions, thumbnails and overrides that nothing yet requires — deferred rather than guessed. Test: `test_shared_store_id_is_reported_not_silently_merged` |
| 16 | 15 files carry version — and 2 carry title — **only** in the parent folder, which `parse(filename: str)` structurally cannot read | `3D/Chinese Alley Environment v1.0/ChineseAlley_Builtin_2021.3.6f1.unitypackage`. The brainstorm listed "folder hint" as a parse output the signature could not receive | Signature is `parse(rel_path)`; `folder_hint` recorded; 15 fixtures |
| 17 | `(PSD)` / `(Source)` / `(PRO)` are product-tier discriminators sharing an extension with their sibling, so the `format-pair` guard cannot fire | `GUI Pro - Simple Casual (PSD) v1.0.7` 259 MB vs `GUI Pro - Simple Casual v1.0.7` 94 MB, both `.unitypackage` | Discriminators retained in the identity key; classified parenthetical table |
| 18 | Thumbnail mirror had no scheme, host, content-type, or size validation, and derived its filename from a scraped URL tail | Zero matches for `allowlist\|content-type\|scheme` anywhere in the plan | Host allowlist, no redirects, content-type check, 2 MB cap, id-only filename |
| 19 | Manifests non-atomic, inside the synced root, single mutable file — a second run orphans the prior batch | No atomic-write or cross-run test anywhere | Moot: no manifests. `assets.json` atomic |
| 20 | Phase 5 bought levels 2-3 that the viewer already delivers as facets | 796/861 (**92.5%**) already in correct store level-1 folders; only 65 root strays | Reorg **cut** |
| 21 | Empty-directory pruning was outside the undo chain and already ambiguous at baseline | 2 directories already empty, incl. `Tools/FImpossible Creations` — a folder the plan named for dissolution | Moot: no pruning |
| 22 | 21 modules where the approved brainstorm design was **one file**; 9 single-function modules, 4 untested under `mode: tdd` | `brainstorm-summary.md:154` specifies `tools/unity-asset-index.py` | 3 files |
| 23 | Quarantine folder was in the scanner skip-list, so 17 files would vanish from the index entirely, contradicting the 861/861 criterion | Skip-list included `_Quarantine/`; `versions[].quarantined` was dead | Moot: no quarantine |

## Rejected

| Finding | Reason |
|---|---|
| "Threshold inversion: true match `POLYGON - Elven Realm…` scores 0.762, below false `Pure Nature` at 0.773" (Security Adversary) | Did not reproduce. Applying the tail-stripping the plan specifies, the true pair scores **1.000** and the false pairs 0.761 / 0.708 — cleanly separable. The reviewer skipped a step of the algorithm. **The structural concern was accepted anyway** via the Assumption Destroyer's stronger evidence (`Epic Toon VFX 2`↔`3` at 0.933), which does invert. |

## Corrections to the predecessor's own numbers

| Claim | Actual |
|---|---|
| 846/852 dataless, 6 materialized | 845/852 dataless, **7** materialized `.unitypackage` + 1 `.zip`; drifts daily |
| "5.5 GB = 1.9% of 293.8 GB" | **1.74%** — decimal GB divided by binary GiB. Library is 315.5 GB = 293.8 GiB |
| "all 27 duplicate groups triaged" | ≥29 by title; **11 by size-collision totalling 15.21 GB** |
| "Exactly 17 files in `_Quarantine/`" | ≥18 (`UMotion Pro` p04/p02), ≥20 with `Horse Animset Pro` |
| "five more under 100 KB" | Lists 7 names. **7** files are under 100,000 B; `Favorites Tabs` (100,095 B) and `Asset Cleaner PRO` (102,267 B) fall in the decimal/binary ambiguity band. Reviewer said 8 — also wrong |
| Data-model example `Amplify Shader Editor v1.9.9.12.unitypackage` | No such local file. Local is `v1.9.9.9 (08 Apr 2026)`; `1.9.9.12` is the upstream version |
| "Slug-only and id-only URLs 404 — no redirect shortcut" | id-only 404s, but a **fabricated category path + valid id returns 301 → 200 at the correct canonical URL**. This became the new design's foundation |

## Unresolved — carried into the new plan

1. Brave 429s at ~request 5. Resumable multi-hour background job, or add a second transport?
2. `66 Creatures Super Mega` — 4.16 GB size-identical pair **in the same folder**, so no path heuristic discriminates. Which copy is authoritative?
3. Byte-identity of any duplicate is unprovable without hydrating (~10 GB). All duplicate identity is recorded as `size+name, unhashed`.
4. Non-store archives (`Kenney Game Assets All-in-1.zip`, `BattleSimulator.zip`, `SrRubfish_VFX_02`, `WM_Animset`) — overrides or leave unenriched?
