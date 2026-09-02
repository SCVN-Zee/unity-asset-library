---
title: Clean superseded Unity package versions
date: 2026-09-01
summary: Removed 26 superseded archives and regenerated the asset index at 854 files / 844 assets.
---

# Clean superseded Unity package versions

## What happened
The configured Unity Asset Store vault had 880 archives, including 26 removable superseded versions across 24 asset families. The index cleanup had to distinguish version families from exact duplicates, preserve pipeline/discriminator variants, and fail closed on ties or unparseable versions.

## Decision
Use highest semantic version, then release date for equal-version ties; keep unresolved exact ties and unparseable families. Cleanup stages validated archives under the excluded _Quarantine directory, verifies identities and manifests, removes only isolated originals, and reconciles the index after partial destructive failures.

## Result
Removed 26 archives (7,877,694,079 bytes). The configured vault now has 854 archives; regenerated state has 854 files and 844 assets. All 234 unit tests pass.

## Next steps
No follow-up cleanup is required. Future downloads should rerun the dry-run cleanup report before applying it.

> Historical work record — not durable authority. Prefer docs/specs/ADRs for current decisions.
