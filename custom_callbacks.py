"""
Disk-backed running cost tracker for the minimal gateway -- no database needed.

Wired in by app.py via litellm.callbacks = [custom_handler_instance]
(litellm's stable public callback list), not litellm_settings.callbacks
(that string-based dotted-import mechanism is part of litellm/proxy/'s
config loader, which this gateway does not have).

What it does:
  - On every call (success or failure), litellm already computes
    `response_cost` in-process -- this callback just reads that number out
    of kwargs.
  - Keeps an in-memory dict of running totals keyed by "provider|model".
  - A background thread flushes that dict to costs.tab every FLUSH_SECONDS,
    tab-separated, so you can tail/cron/import it however you like.
"""

import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone

from litellm.integrations.custom_logger import CustomLogger

COSTS_FILE = os.environ.get("COSTS_FILE", "costs.tab")
FLUSH_SECONDS = int(os.environ.get("COSTS_FLUSH_SECONDS", "60"))

_lock = threading.Lock()
_running_totals = defaultdict(lambda: {"requests": 0, "cost": 0.0})


def _load_costs_tab():
    if not os.path.exists(COSTS_FILE):
        return
    with open(COSTS_FILE) as f:
        lines = f.readlines()
    for line in lines[1:]:  # skip header
        fields = line.strip().split(" ")
        if len(fields) < 4:
            continue
        provider, model, requests, cost = fields[0], fields[1], fields[2], fields[3]
        key = f"{provider}|{model}"
        _running_totals[key]["requests"] = int(requests)
        _running_totals[key]["cost"] = float(cost)


_load_costs_tab()


def _key_for(kwargs) -> str:
    litellm_params = kwargs.get("litellm_params", {}) or {}
    provider = litellm_params.get("custom_llm_provider", "unknown")
    model = kwargs.get("model", "unknown")
    return f"{provider}|{model}"


def _record(kwargs, response_cost: float):
    key = _key_for(kwargs)
    with _lock:
        _running_totals[key]["requests"] += 1
        _running_totals[key]["cost"] += response_cost or 0.0
    print(f"[COST TRACKING] {key} requests={_running_totals[key]['requests']} total_cost={_running_totals[key]['cost']:.6f}")


def _flush_loop():
    while True:
        time.sleep(FLUSH_SECONDS)
        _write_costs_tab()


def _write_costs_tab():
    with _lock:
        snapshot = {k: dict(v) for k, v in _running_totals.items()}

    tmp_path = COSTS_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        f.write("provider model requests estimated_cost_usd updated_at\n")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        for key, totals in sorted(snapshot.items()):
            provider, model = key.split("|", 1)
            f.write(f"{provider} {model} {totals['requests']} {totals['cost']:.6f} {ts}\n")
    os.replace(tmp_path, COSTS_FILE)  # atomic on same filesystem


class RunningCostTracker(CustomLogger):
    def __init__(self):
        threading.Thread(target=_flush_loop, daemon=True).start()

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        _record(kwargs, kwargs.get("response_cost"))

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        _record(kwargs, kwargs.get("response_cost"))

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        _record(kwargs, 0.0)


custom_handler_instance = RunningCostTracker()

