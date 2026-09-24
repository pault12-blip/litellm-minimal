"""
Cost + drift capture for the minimal gateway -- no database.

Wired in by app.py via litellm.callbacks = [custom_handler_instance]
(litellm's stable public callback list), not litellm_settings.callbacks
(that string-based dotted-import mechanism belongs to litellm/proxy/'s
config loader, which this gateway does not have).

Two independent things happen here, both append-only, both driven by the
same two hooks:
  - Every call appends one line to FOCUS/costs_ledger.tsv (see focus.py).
    Reconciliation against real provider balances and the FOCUS CSV export
    both run separately, in focus.py, as a standalone periodic script.
  - Any call app.py tagged with drift_correlation_id/drift_role (the
    silent-model mirroring this gateway drives itself -- see config.py and
    drift.py for why we never use litellm.Router's own silent_model
    dispatch) gets routed to drift.record_half(), which joins the primary
    and silent halves on disk and appends the combined pair to
    DRIFT/ledger.jsonl.
"""

from litellm.integrations.custom_logger import CustomLogger

from focus import append_ledger
from drift import record_half


def _key_for(kwargs) -> tuple[str, str]:
    litellm_params = kwargs.get("litellm_params", {}) or {}
    provider = litellm_params.get("custom_llm_provider", "unknown")
    model = kwargs.get("model", "unknown")
    return provider, model


def _n_tokens(kwargs) -> int:
    slo = kwargs.get("standard_logging_object") or {}
    return int(slo.get("total_tokens") or 0)


def _record_cost(kwargs, response_cost: float):
    provider, model = _key_for(kwargs)
    spend = response_cost or 0.0
    n_tokens = _n_tokens(kwargs)
    append_ledger(provider, model, spend, n_tokens)
    print(f"[COST TRACKING] {provider}|{model} spend={spend:.6f} tokens={n_tokens}")


def _record_drift(kwargs, response_obj, error):
    metadata = kwargs.get("litellm_params", {}).get("metadata") or {}
    correlation_id = metadata.get("drift_correlation_id")
    if correlation_id is None:
        return
    provider, model = _key_for(kwargs)
    record_half(
        correlation_id=correlation_id,
        role=metadata.get("drift_role", "unknown"),
        model_name=model,
        provider=provider,
        prompt=kwargs.get("messages"),
        response_obj=response_obj,
        error=error,
    )


class RunningCostTracker(CustomLogger):
    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        _record_cost(kwargs, kwargs.get("response_cost"))
        _record_drift(kwargs, response_obj, error=None)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        _record_cost(kwargs, kwargs.get("response_cost"))
        _record_drift(kwargs, response_obj, error=None)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        _record_cost(kwargs, 0.0)
        _record_drift(kwargs, response_obj, error=kwargs.get("exception"))


custom_handler_instance = RunningCostTracker()
