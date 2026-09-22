"""
Ledger-only cost tracker for the minimal gateway -- no database.

Wired in by app.py via litellm.callbacks = [custom_handler_instance]
(litellm's stable public callback list), not litellm_settings.callbacks
(that string-based dotted-import mechanism belongs to litellm/proxy/'s
config loader, which this gateway does not have).

This is intentionally the ONLY thing the gateway process does: append one
line per call to FOCUS/costs_ledger.tsv (see focus.py). Reconciliation
against real provider balances and the FOCUS CSV export both happen in
focus.py, run as a separate, periodic, standalone script -- never inside
this process.
"""

from litellm.integrations.custom_logger import CustomLogger

from focus import append_ledger


def _key_for(kwargs) -> tuple[str, str]:
    litellm_params = kwargs.get("litellm_params", {}) or {}
    provider = litellm_params.get("custom_llm_provider", "unknown")
    model = kwargs.get("model", "unknown")
    return provider, model


def _n_tokens(kwargs) -> int:
    slo = kwargs.get("standard_logging_object") or {}
    return int(slo.get("total_tokens") or 0)


def _record(kwargs, response_cost: float):
    provider, model = _key_for(kwargs)
    spend = response_cost or 0.0
    n_tokens = _n_tokens(kwargs)
    append_ledger(provider, model, spend, n_tokens)
    print(f"[COST TRACKING] {provider}|{model} spend={spend:.6f} tokens={n_tokens}")


class RunningCostTracker(CustomLogger):
    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        _record(kwargs, kwargs.get("response_cost"))

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        _record(kwargs, kwargs.get("response_cost"))

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        _record(kwargs, 0.0)


custom_handler_instance = RunningCostTracker()

