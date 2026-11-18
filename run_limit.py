"""Daily run limit for LaunchScout: at most MAX_RUNS_PER_DAY crew runs per day (default 3).

- A run is counted the moment it starts (failed runs count too: they also spend API credit).
- The count resets at midnight India time (IST, UTC+5:30).
- The counter lives in output/run_limit.json and is updated under a lock, so two people
  pressing Run at the same moment cannot both slip past the limit.

Settings (environment):
    MAX_RUNS_PER_DAY    runs allowed per day, default 3 (0 = no limit)
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

LEDGER_PATH = Path(__file__).parent / "output" / "run_limit.json"
IST = timezone(timedelta(hours=5, minutes=30))
_lock = threading.RLock()


class RunLimitReached(RuntimeError):
    """Raised when today's runs are used up."""


def limit_per_day() -> int:
    try:
        return max(0, int(os.getenv("MAX_RUNS_PER_DAY", "3")))
    except ValueError:
        return 3


def _today() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def _load() -> dict[str, Any]:
    try:
        data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if data.get("day") != _today():  # new day: start from zero
        data = {"day": _today(), "runs": []}
    return data


def _save(data: dict[str, Any]) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(LEDGER_PATH)  # atomic: a crash never leaves a half-written file


def status() -> dict[str, Any]:
    with _lock:
        data = _load()
    limit = limit_per_day()
    used = len(data["runs"])
    return {
        "day": data["day"], "limit": limit, "used": used, "runs": data["runs"],
        "runs_left": max(limit - used, 0) if limit else None,
        "can_start": (not limit) or used < limit,
    }


def start_run(idea: str, city: str) -> int:
    """Reserve one run for today. Returns its index; raises RunLimitReached when none are left."""
    with _lock:
        data = _load()
        limit = limit_per_day()
        if limit and len(data["runs"]) >= limit:
            raise RunLimitReached(
                f"Daily limit reached: {limit} of {limit} runs used today. "
                "New runs open again at midnight (India time).")
        data["runs"].append({"at": datetime.now(IST).isoformat(timespec="seconds"),
                             "idea": idea[:80], "city": city[:40], "completed": None})
        _save(data)
        return len(data["runs"]) - 1


def finish_run(index: int, completed: bool, note: str = "") -> None:
    with _lock:
        data = _load()
        if 0 <= index < len(data["runs"]):
            data["runs"][index].update(completed=completed, note=note[:120])
            _save(data)
