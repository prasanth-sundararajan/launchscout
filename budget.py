"""Monthly spending cap for LaunchScout (default $0.30 per calendar month, UTC).

How it is enforced:
- Every LLM call's real token usage (CrewAI's LLMCallCompletedEvent) and every
  Serper request is priced and added to a ledger file the moment it happens.
- A run only starts if the money left this month covers a typical run.
- A CrewAI before_llm_call hook blocks any further LLM call once the cap is
  reached, so a run in progress stops instead of overspending.
At most the single call already in flight can push spend slightly past the cap.

Settings (environment):
    MONTHLY_BUDGET_USD      cap per month, default 0.30 (0 = no cap)
    LLM_PRICE_INPUT_PER_M   override $ per 1M input tokens for LLM_MODEL
    LLM_PRICE_CACHED_PER_M  override $ per 1M cached input tokens
    LLM_PRICE_OUTPUT_PER_M  override $ per 1M output tokens
    SERPER_COST_PER_CALL    $ per Serper request, default 0.001
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LEDGER_PATH = Path(__file__).parent / "output" / "budget.json"

# $ per 1M tokens: (input, cached input, output). Check your provider's price page;
# override with the LLM_PRICE_* variables if they differ.
PRICES_PER_M: dict[str, tuple[float, float, float]] = {
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "gpt-4.1": (2.00, 0.50, 8.00),
    "gpt-4o-mini": (0.15, 0.075, 0.60),
    "gpt-4o": (2.50, 1.25, 10.00),
    "claude-opus-5-5": (4.00, 0.40, 20.00),
    "claude-sonnet-5-5": (2.00, 0.10, 10.00),
    "claude-haiku-5-5": (0.10, 0.01, 0.50),
}
DEFAULT_RUN_ESTIMATE = 0.08   # used until a real run has been measured
# Serper requests made by each search tool call
SERPER_CALLS_PER_TOOL = {"serper": 1, "google maps reviews": 3}

_lock = threading.RLock()


def limit_usd() -> float:
    try:
        return max(0.0, float(os.getenv("MONTHLY_BUDGET_USD", "0.30")))
    except ValueError:
        return 0.30


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _load() -> dict[str, Any]:
    try:
        data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if data.get("month") != _month():  # new month: start from zero, keep last month's total
        data = {"month": _month(), "spent_usd": 0.0, "llm_usd": 0.0, "serper_usd": 0.0,
                "runs": [], "previous": {"month": data.get("month"),
                                         "spent_usd": data.get("spent_usd", 0.0)} if data else None}
    return data


def _save(data: dict[str, Any]) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(LEDGER_PATH)  # atomic: a crash never leaves a half-written ledger


def _add(kind: str, usd: float) -> None:
    with _lock:
        data = _load()
        data["spent_usd"] = round(data["spent_usd"] + usd, 6)
        data[f"{kind}_usd"] = round(data.get(f"{kind}_usd", 0.0) + usd, 6)
        _save(data)


def price_for(model: str) -> tuple[float, float, float]:
    name = model.split("/")[-1]
    base = PRICES_PER_M.get(name) or next(
        (v for k, v in PRICES_PER_M.items() if name.startswith(k)), (2.00, 0.50, 8.00))
    def env(var: str, default: float) -> float:
        try:
            return float(os.environ[var])
        except (KeyError, ValueError):
            return default
    return (env("LLM_PRICE_INPUT_PER_M", base[0]), env("LLM_PRICE_CACHED_PER_M", base[1]),
            env("LLM_PRICE_OUTPUT_PER_M", base[2]))


def call_cost(usage: dict[str, Any], model: str) -> float:
    """$ for one LLM call from CrewAI's usage dict."""
    inp = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    out = usage.get("completion_tokens") or usage.get("output_tokens") or 0
    cached = usage.get("cached_prompt_tokens") or usage.get("cache_read_input_tokens") or 0
    p_in, p_cached, p_out = price_for(model)
    return ((max(inp - cached, 0) * p_in) + cached * p_cached + out * p_out) / 1_000_000


def status() -> dict[str, Any]:
    with _lock:
        data = _load()
    limit = limit_usd()
    costs = [r["cost_usd"] for r in data["runs"] if r.get("completed")]
    estimate = round(max(costs) * 1.2, 4) if costs else DEFAULT_RUN_ESTIMATE
    remaining = max(limit - data["spent_usd"], 0.0) if limit else float("inf")
    return {
        "month": data["month"], "limit": limit, "spent": data["spent_usd"],
        "llm": data.get("llm_usd", 0.0), "serper": data.get("serper_usd", 0.0),
        "remaining": remaining, "run_estimate": estimate, "runs": data["runs"],
        "can_start": (not limit) or remaining >= estimate,
        "exhausted": bool(limit) and data["spent_usd"] >= limit,
        "runs_left": (int(remaining // estimate) if limit and estimate else None),
    }


def record_run(idea: str, city: str, cost: float, completed: bool, note: str = "") -> None:
    with _lock:
        data = _load()
        data["runs"].append({
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "idea": idea[:80], "city": city[:40], "cost_usd": round(cost, 5),
            "completed": completed, "note": note,
        })
        _save(data)


def spent_now() -> float:
    with _lock:
        return _load()["spent_usd"]


# ------------------------------------------------------------- CrewAI wiring
_installed = False


def install() -> None:
    """Register the cost tracker and the hard-stop hook once per process."""
    global _installed
    if _installed:
        return
    from crewai.events import LLMCallCompletedEvent, ToolUsageFinishedEvent, crewai_event_bus
    from crewai.hooks.llm_hooks import register_before_llm_call_hook

    def on_llm_call(source: Any, event: Any) -> None:
        usage = getattr(event, "usage", None) or {}
        model = getattr(event, "model", None) or os.getenv("LLM_MODEL", "")
        if usage:
            _add("llm", call_cost(usage, str(model)))

    def on_tool(source: Any, event: Any) -> None:
        name = str(getattr(event, "tool_name", "")).lower().replace("_", " ")
        calls = next((n for key, n in SERPER_CALLS_PER_TOOL.items() if key in name), 0)
        if calls and not getattr(event, "from_cache", False):
            _add("serper", calls * float(os.getenv("SERPER_COST_PER_CALL", "0.001")))

    def stop_when_over_budget(context: Any) -> bool | None:
        limit = limit_usd()
        if limit and spent_now() >= limit:
            return False  # CrewAI blocks the call; the run stops
        return None

    crewai_event_bus.register_handler(LLMCallCompletedEvent, on_llm_call)
    crewai_event_bus.register_handler(ToolUsageFinishedEvent, on_tool)
    register_before_llm_call_hook(stop_when_over_budget)
    _installed = True


class BudgetExceeded(RuntimeError):
    """Raised before a run starts when this month's money left cannot cover it."""
