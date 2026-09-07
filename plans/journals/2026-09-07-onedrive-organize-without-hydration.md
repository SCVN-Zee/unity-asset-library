---
title: OneDrive organize without hydration
date: 2026-09-07
summary: Replaced hard-link moves with exclusive native rename; verified on real online-only OneDrive files.
---

# OneDrive organize without hydration

## Root cause
Organize used os.link followed by os.unlink. A disposable 65,536-byte OneDrive control changed from SF_DATALESS/zero blocks to 128 allocated blocks after os.link. Native renamex_np flags 0x14 preserved dataless status and identity, including rollback.

## Delivered
Implemented native exclusive no-follow rename in the organizer, with attempted-move ledger and no copy fallback. Preserved CLI/HTTP schemas, no-overwrite behavior and index refresh semantics. Independent review caught a missing source-identity check when rollback destination was absent; fixed with same-size replacement regression.

## Verification
Actual CLI dry-run/apply and real index update passed locally and in a disposable OneDrive directory; cloud file stayed online-only immediately and after 10 seconds. Final engine rollback also kept zero blocks. Python: 259 tests pass, organizer subset 31. Desktop bootstrap and npm build pass. Existing index test ResourceWarnings and Vite chunk-size advisory remain. Owned cloud probes and smoke script removed. No real asset collection was organized; no commit or publication.

## Usage
Restart the desktop/backend before using Organize to load the changed module. Plan: plans/260906-1552-onedrive-native-organize/plan.md. No Graph integration was needed.

> Historical work record — not durable authority. Prefer docs/specs/ADRs for current decisions.
