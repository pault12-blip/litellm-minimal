"""
Cost ledger + scheduled FOCUS export for the minimal gateway -- no database.

Wired in by app.py via litellm.callbacks = [custom_handler_instance]
(litellm's stable public callback list), not litellm_settings.callbacks
(that string-based dotted-import mechanism belongs to litellm/proxy/'s
config loader, which this gateway does not have).

On every call, appends one line to costs_ledger.tsv (see focus.py):
"timestamp provider model spend". A background thread periodically calls
focus.export_focus_csv(), which reads that ledger plus checkpoints.tsv
(manually-appended real billing figures) and writes a FOCUS 1.2 CSV, with
BilledCost corrected against the last checkpoint interval and EffectiveCost
left as the raw, uncorrected estimate. See focus.py for the reconciliation
math.
"""

import os
import threading
import time

from litellm.integrations.custom_logger import CustomLogger

from focus import append_ledger, export_focus_csv

LEDGER_FILE = os.environ.get("COSTS_LEDGER_FILE", "FOCUS/costs_ledger.tsv")
CHECKPOINTS_FILE = os.environ.get("FOCUS_CHECKPOINT_FILE", "FOCUS/checkpoints.tsv")
FOCUS_EXPORT_FILE = os.environ.get("FOCUS_EXPORT_FILE", "FOCUS/focus_export.csv")
FOCUS_EXPORT_SECONDS = int(os.environ.get("FOCUS_EXPORT_SECONDS", str(7 * 24 * 60 * 60)))  # weekly


def _key_for(kwargs) -> tuple[str, str]:
    litellm_params = kwargs.get("litellm_params", {}) or {}
    provider = litellm_params.get("custom_llm_provider", "unknown")
    model = kwargs.get("model", "unknown")
    return provider, model


def _record(kwargs, response_cost: float):
    provider, model = _key_for(kwargs)
    spend = response_cost or 0.0
    append_ledger(LEDGER_FILE, provider, model, spend)
    print(f"[COST TRACKING] {provider}|{model} spend={spend:.6f}")


def _export_loop():
    while True:
        time.sleep(FOCUS_EXPORT_SECONDS)
        try:
            n = export_focus_csv(LEDGER_FILE, CHECKPOINTS_FILE, FOCUS_EXPORT_FILE)
            print(f"[FOCUS EXPORT] wrote {n} rows to {FOCUS_EXPORT_FILE}")
        except Exception as e:
            print(f"[FOCUS EXPORT] failed: {e}")


class RunningCostTracker(CustomLogger):
    def __init__(self):
        threading.Thread(target=_export_loop, daemon=True).start()

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        _record(kwargs, kwargs.get("response_cost"))

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        _record(kwargs, kwargs.get("response_cost"))

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        _record(kwargs, 0.0)


custom_handler_instance = RunningCostTracker()

