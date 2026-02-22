#!/usr/bin/env python3
"""Utilities for robust JSON state files."""

from __future__ import annotations

import copy
import json
import os
import time
from pathlib import Path
from typing import Any


def load_json_state(path: Path, default: Any) -> Any:
    """Load JSON state, falling back to default on parse errors."""
    if not path.exists():
        return copy.deepcopy(default)

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        ts = int(time.time())
        backup = path.with_name(f"{path.name}.corrupt.{ts}")
        try:
            path.replace(backup)
        except OSError:
            pass
        return copy.deepcopy(default)


def save_json_state(path: Path, data: Any) -> None:
    """Atomically persist JSON state to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}")
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
