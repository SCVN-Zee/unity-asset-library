# Unity Asset Index — Plan Killed by Red Team, Bug Killed by a Reviewer's Fail-Closed Check

**Date**: 2026-07-30
**Severity**: Medium
**Component**: `.index/bin/index_assets.py`, `.index/bin/resolve_store.py` (offline asset index + store enrichment)
**Status**: Ongoing (Phase 1 resolved, Phase 2 blocked on quota)

## What Happened

Built a re-runnable offline index for 861 archived Unity Asset Store packages (315.5 GB, all OneDrive dataless placeholders). Phase 1 complete: 861 archives → 835 assets, 9.26 GB of true duplicates flagged, zero archives opened by the tooling. Phase 2 (store metadata via web search, identity-verified) is code-complete with 122 tests passing, 151/825 eligible assets enriched, the remaining 645 blocked purely by a per-session search quota.

The path here was not straight. Three turning points did the real work.

## The Brutal Truth

The first plan looked fine on paper and got dismantled anyway — that stings less than it should, because dismantling it was correct and cheap compared to shipping it. What actually stings: I wrote the `offers.url` id-verification check **fail-open**. A reviewer made me flip it to fail-closed for an unrelated red-team finding, and that stricter check is the only reason a regex bug that would have silently mislabeled every digit-bearing package's author/category/thumbnail didn't ship. My own permissiveness was the near-miss, not the regex.

## Technical Details

- **Plan kill**: 4-lens red team on the original 5-phase/21-module plan found "quarantine recovers 5.5 GB" was false — all 17 target files are dataless placeholders (`st_blocks == 0`), so moving them frees zero bytes. Three more premises independently false (`tools`/`Tools` same inode silently dropped 191 archives; duplicate census 2.8× incomplete — 5.49 GB claimed vs 15.21 GB measured; similarity-match gate passes `Epic Toon VFX 2` vs `3` at 0.933, above a genuine publisher rename at 0.872). Plan withdrawn, rebuilt as 2 phases / 3 files rather than patched.
- **Search transport**: Brave 429 for 10 consecutive probes over 7.5 min, no recovery. DuckDuckGo html+lite: 202 "anomaly" after ~2 requests. Bing RSS never blocks but returns 0 results for real titles (3 titles × 3 query forms). Fell back to agent-driven `WebSearch`, which then hit a hard 200-call/session quota shared across the parent session and all subagents — wave 1 (5 agents × 30 titles) burned the entire budget.
- **Regex bug**: non-greedy `PACKAGE_URL_RE` captured the wrong package id from any slug containing digits — `2` from `fps-framework-2-0-278978`, `02` from `robots-ultimate-pack-02-cute-series-213777`. Caught by the fail-closed `offers.url` id check added for red-team finding M3. Fixing it moved the verification pilot from 66.7% to 90%.

## What We Tried

- Patching the original plan — rejected; four independently-false foundations meant patch-and-pray would leave undiscovered fifth and sixth premises standing.
- Name/token similarity for store matching — replaced with identity verification (numeric store id round-tripped through a second endpoint) after measuring that no similarity threshold separates true from false matches on this library.
- A hardcoded hydration-count gate — replaced with a path-set diff, because OneDrive rehydrates on its own schedule; a count would have reported 8→9 and named nothing. One archive (`Amplify Impostors v1.0.0`, 207 MB) did materialize mid-session — the diff proved it was OneDrive, not the tool.

## Root Cause Analysis

Every material defect this session — four false plan premises plus the id-capture regex — was surfaced by measurement or by a reviewer demanding stricter validation, never by reasoning about the design beforehand. The plan's authors (including me, on the predecessor) asserted "verified working" and "5.5 GB recovered" without running the check. The regex shipped past my own review because I wrote its guard fail-open, trading correctness for not-blocking-progress.

## Lessons Learned

Design review catches false premises; execution catches code bugs. Neither substitutes for the other. Default to fail-closed on any gate that writes metadata over an existing field — the fail-open instinct is exactly what lets a wrong-id write through silently. Verify by identity, not similarity, whenever siblings share most of a name. Prefer diffs over counts for anything a background process can change without asking. And re-measure every number inherited from a prior doc: two of six claims re-checked this session were wrong (61 vs. actual 66 token-subset pairs; "8 files under 100 KB" vs. actual 7).

## Next Steps

Resume Phase 2 across ~5 more sessions using the 23 pre-generated batch files (or raise `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`) to enrich the remaining 645/825 assets — resume procedure is documented in `IMPLEMENTATION-STATUS.md`. Open decisions still need the user: which copy of the 4.16 GB `66 Creatures Super Mega` duplicate pair is authoritative (no path heuristic discriminates, same folder); whether to accept store-renamed titles or keep as-purchased names for 30 override candidates; whether to collapse the three `ChineseAlley` pipeline variants (one store id, 315413) into one entry.

---

# Addendum — Index Change Tracking (same day)

Plan: `plans/260730-1746-index-change-tracking/` — 2/2 phases, **179 tests passing**.

## Outcome

One offline command, `index_assets.py update`: rescan (0.09 s), diff the previous state,
append `.index/CHANGES.md`, queue newly-appeared assets, re-apply enrichment, re-emit.
Viewer now stamps its own age; queued assets carry a filterable `pending-enrichment` flag;
`export-titles --pending` narrows a search session to just what needs one.

## Two questions answered without writing code

**"861 files → 835 assets, where did the rest go?"** Nowhere. 24 assets group 50 files,
collapsing exactly 26. A fact question, not a design question — worth checking before
building anything.

**"Can git track file changes but not content?"** No — a blob *is* the content. And `.git`
inside this OneDrive folder is actively unsafe: OneDrive syncs `.git/index`, `objects/`,
`refs/`, a mid-write sync corrupts the repo, and macOS OneDrive has no per-subfolder
exclusion. The changelog delivers the same answer with none of that risk. Worth saying no
to clearly rather than building the thing that was asked for.

The measurement that shaped everything: a full rescan is **0.09 seconds**. That dissolved
the entire "how do we efficiently detect changes" question — no file watching, no
incremental scan, no scheduler. Checking is cheaper than deciding whether to check.

## The bugs, and how each was found

| Bug | Found by |
|---|---|
| `update` wiped all 717 enrichments (`build()` regenerates empty metadata; I skipped the enrich step) | The post-run summary printing `0 id-verified` where I expected 717 |
| `scan update` in one call re-wiped it — reachable via `scan emit update`, the only form the docstring shows | Code review, not me |
| `unittest.main()` mid-file in **both** test modules: direct runs reported "OK" on 37/81 and 42/81, silently skipping the 717→0 regression test itself | A test I wrote for the first file, which then failed on the second |
| `append_changelog` sliced by `len(CHANGELOG_HEADER)` — would destroy all history the moment the header text changed | Code review |
| Corrupt `pending-enrichment.json` silently discarded every entry | Code review |

## Lesson, sharpened

The previous entry said every defect came from running the thing or from a reviewer being
stricter than I wanted. This addendum adds a third source: **a test written for one file
finding the same bug in another.** The mid-file `unittest.main()` guard had been hiding half
of each test file across two plans. Discovery never surfaced it; only an explicit assertion
about file structure did.

And the enrichment wipe is worth remembering precisely because no test caught it — I noticed
a number that was 717 the last time I looked and was now 0. Watching values move in the
wrong direction is a real debugging instrument, not a substitute for tests.
