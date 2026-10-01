"""Reconstructible pending store-enrichment queue."""

from datetime import datetime, timezone
import json
import os
import shutil
import uuid


def pending_payload(data, cache, existing=None):
    """Build pending records from current assets/cache, preserving first_seen."""
    old = {}
    if isinstance(existing, dict) and isinstance(existing.get("pending"), list):
        for entry in existing["pending"]:
            if isinstance(entry, dict) and isinstance(entry.get("asset_key"), str):
                old[entry["asset_key"]] = entry
    resolved = (cache or {}).get("resolved", {})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pending = []
    for asset in (data or {}).get("assets", []):
        key = asset.get("asset_key")
        if not key or asset.get("non_store") or (resolved.get(key) or {}).get("status") == "resolved":
            continue
        prior = old.get(key, {})
        first_seen = prior.get("first_seen")
        try:
            datetime.strptime(first_seen, "%Y-%m-%d")
        except (TypeError, ValueError):
            first_seen = today
        pending.append({
            "asset_key": key,
            "title": asset.get("local_name") or asset.get("name") or "",
            "first_seen": first_seen,
            "previously_unresolved": bool(prior.get("previously_unresolved") is True or resolved.get(key)),
        })
    return {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pending": sorted(pending, key=lambda entry: entry["asset_key"])}


def load_existing(path):
    """Read queue hints only; preserve malformed bytes before reconstruction."""
    try:
        with open(path, encoding="utf-8") as fh:
            candidate = json.load(fh)
        if (not isinstance(candidate, dict) or not isinstance(candidate.get("pending"), list)
                or any(not isinstance(row, dict) or not isinstance(row.get("asset_key"), str)
                       for row in candidate["pending"])):
            raise ValueError("invalid queue shape")
        return candidate
    except FileNotFoundError:
        return None
    except (ValueError, UnicodeDecodeError):
        backup = path + ".corrupt"
        if os.path.exists(backup):
            backup += "." + uuid.uuid4().hex
        shutil.copyfile(path, backup)
        return None


def reconcile(path, data, cache):
    """Persist derived pending work. Caller must hold the shared state lock."""
    payload = pending_payload(data, cache, load_existing(path))
    from index_assets import write_atomic
    write_atomic(path, json.dumps(payload, indent=1, sort_keys=True))
    return [entry["asset_key"] for entry in payload["pending"]]
