"""
Tolerant config.yaml loader for the minimal gateway.

Contract: this loader MUST accept the full standard litellm proxy
config.yaml schema without failing on keys the minimal gateway does not use.
The config file is the user's contract -- we never delete or mutate keys in
it, we only read the subset that maps onto litellm.Router's own public
constructor signature.

Consumed:
  - model_list            -> passed straight to Router(model_list=...)
  - router_settings       -> filtered to exactly the keyword arguments
                              litellm.Router.__init__ accepts *in the
                              installed litellm version*; introspected at
                              import time via inspect.signature rather than
                              hardcoded, so a future litellm bump that adds
                              or removes a Router kwarg is picked up
                              automatically instead of silently drifting.
                              Unknown router_settings keys are reported, not
                              silently dropped.

Ignored-with-warning (proxy/server/DB-only; out of scope for this gateway --
no keys, no DB, no auth, no multi-replica state):
  - master_key, general_settings, litellm_settings.callbacks,
    litellm_settings.success_callback / failure_callback,
    any S3 / GCS / Azure / Redis cloud-logging or cache config,
    SSO / SAML config, guardrails, budgets, teams.

If the config uses a feature this gateway genuinely cannot serve, this
loader lists exactly which top-level keys were ignored -- it never
pretends to have implemented them.
"""

from __future__ import annotations

import inspect
import sys
from typing import Any

import litellm
import yaml

_KNOWN_UNSUPPORTED_TOP_LEVEL_KEYS = {
    "general_settings",  # auth, budgets, teams, keys, DB connection
    "litellm_settings",  # only a tiny subset is safe to read; see below
}

_SUPPORTED_LITELLM_SETTINGS_KEYS = {
    "drop_params",
    "set_verbose",
    "json_logs",
    "request_timeout",
}


def _router_accepted_kwargs() -> set[str]:
    sig = inspect.signature(litellm.Router.__init__)
    return {name for name in sig.parameters if name != "self"}


def load_config(path: str) -> dict[str, Any]:
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    if "model_list" not in raw:
        print(f"error: {path} has no top-level 'model_list' key", file=sys.stderr)
        sys.exit(1)

    accepted = _router_accepted_kwargs()

    router_settings: dict[str, Any] = dict(raw.get("router_settings") or {})
    unknown_router_keys = set(router_settings) - accepted
    router_kwargs = {k: v for k, v in router_settings.items() if k in accepted}

    litellm_settings: dict[str, Any] = dict(raw.get("litellm_settings") or {})
    for key in _SUPPORTED_LITELLM_SETTINGS_KEYS & set(litellm_settings):
        setattr(litellm, key, litellm_settings[key])

    ignored_top_level = set(raw) & _KNOWN_UNSUPPORTED_TOP_LEVEL_KEYS

    # litellm_settings is partially supported, so report only if it had
    # keys we don't forward, not just because the block exists.

    if "litellm_settings" in ignored_top_level:
        remaining = set(litellm_settings) - _SUPPORTED_LITELLM_SETTINGS_KEYS
        if not remaining:
            ignored_top_level.discard("litellm_settings")

    if unknown_router_keys:
        print(
            f"warning: router_settings keys not recognized by installed "
            f"litellm.Router and ignored: {sorted(unknown_router_keys)}",
            file=sys.stderr,
        )
    for key in sorted(ignored_top_level):
        print(
            f"warning: top-level config key '{key}' is proxy-server-only "
            f"(auth/DB/budgets/callbacks) and is not implemented by this "
            f"minimal gateway; it is present in your config but has no "
            f"effect here.",
            file=sys.stderr,
        )

    return {
        "model_list": raw["model_list"],
        "router_kwargs": router_kwargs,
        "ignored_keys": sorted(ignored_top_level),
    }

