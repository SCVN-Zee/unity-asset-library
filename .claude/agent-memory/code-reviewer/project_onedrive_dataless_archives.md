---
name: onedrive-dataless-archives
description: Never open/read/hash any .unitypackage or .zip in this Unity asset folder — 845 of 852 are OneDrive online-only placeholders and reading them downloads up to 294 GiB
metadata:
  type: project
---

In `Game Assets/Unity/`, the 852 `.unitypackage` + 9 `.zip` files total ~293.8 GiB
logical but only ~7 are materialized locally (`stat -f %b` → `st_blocks == 0` on the
rest). Any tool that **opens** an archive triggers OneDrive hydration and downloads it.

**Why:** verified 2026-07-30 — `find . -name '*.unitypackage' -exec stat -f '%b' {} +`
returned `st_blocks == 0` for 845 of 852 files. A conventional indexer that opens each
archive to inspect contents would pull ~294 GiB of the user's metered cloud storage.
The archives contain no store metadata anyway (tar of GUID dirs), so opening them buys
nothing.

**How to apply:** any script, review, or analysis touching this tree uses `os.walk` +
`os.stat` / `find` / `stat` / `ls` / `wc` on metadata **only**. Never `cat`, `tarfile`,
`zipfile`, `gzip`, `shasum`, `md5`, or `open()`. Content-hash-based dedupe is off the
table here — use size + filename. Note the materialized count is **not stable** (OneDrive
hydrates/dehydrates on its own schedule), so never hardcode it as an assertion; capture a
baseline at run start and compare deltas instead.

Also: `tools` and `Tools` are the **same directory** (inode 1000698, case-insensitive
APFS) and it holds 191 asset packages — do not treat `tools/` as a free namespace for
scripts here.
