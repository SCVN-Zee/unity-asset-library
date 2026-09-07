#!/usr/bin/env python3
"""Shared, best-effort progress reporting for engine operations."""

import json
import sys


def emit_progress(callback, stage, completed=0, total=None, current_item=None, **counts):
    """Deliver one normalized progress event without affecting the operation."""
    if callback is None:
        return None
    event = {
        "stage": stage,
        "completed": completed,
        "total": total,
        "current_item": current_item,
        "counts": counts,
    }
    try:
        callback(event)
    except Exception:
        # Observers are telemetry only; never turn a successful file operation into
        # a failed one because a client disappeared or rendered an event badly.
        return None
    return None


def json_progress(event):
    """Write one machine-readable progress event to stdout and flush it."""
    print("UL_PROGRESS " + json.dumps(event, sort_keys=True), flush=True)
