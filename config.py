# keys.tab (alongside config.yaml, path overridable via LITELLM_GATEWAY_KEYS_FILE)
# holds "provider api_key" lines (space-separated) and fills in
# litellm_params.api_key for any model that leaves it unset or set to a
# CHANGE_ME_* placeholder.

from __future__ import annotations

import inspect
import os
import sys
from typing import Any

import litellm
import yaml

_DEFAULT_KEYS_FILE = "keys.tab"

_KNOWN_UNSUPPORTED_TOP_LEVEL_KEYS = {
    "general_settings",
    "litellm_settings",
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


def _load_provider_keys(path: str) -> dict[str, str]:
    if not os.path.exists(path):
        return {}

    keys: dict[str, str] = {}
    with open(path) as f:
        for lineno, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            fields = stripped.split(" ")
            if len(fields) != 2 or not fields[0] or not fields[1]:
                print(
                    f"error: {path}:{lineno}: malformed line, expected "
                    f"'provider api_key': {line!r}",
                    file=sys.stderr,
                )
                sys.exit(1)
            provider, api_key = fields
            keys[provider] = api_key
    return keys


def _needs_key_from_table(litellm_params: dict[str, Any]) -> bool:
    api_key = litellm_params.get("api_key")
    if not api_key:
        return True
    return isinstance(api_key, str) and api_key.startswith("CHANGE_ME_")


def _resolve_provider_keys(model_list: list[dict[str, Any]], provider_keys: dict[str, str]) -> None:
    for entry in model_list:
        litellm_params = entry.get("litellm_params") or {}
        if not _needs_key_from_table(litellm_params):
            continue

        model_string = litellm_params.get("model", "")
        prefix = model_string.split("/", 1)[0] if "/" in model_string else None

        if prefix is None or prefix not in provider_keys:
            model_name = entry.get("model_name", "<unnamed>")
            missing = repr(prefix) if prefix else "<no prefix in model string>"
            print(
                f"error: model '{model_name}' (model={model_string!r}) has no "
                f"api_key and no matching entry for provider "
                f"{missing} in keys.tab -- refusing to start with a "
                f"null/placeholder key.",
                file=sys.stderr,
            )
            sys.exit(1)

        litellm_params["api_key"] = provider_keys[prefix]
        entry["litellm_params"] = litellm_params


def _extract_silent_pairs(model_list: list[dict[str, Any]]) -> dict[str, str]:
    """Reads litellm_params.silent_model off each deployment, then REMOVES
    it before the config ever reaches litellm.Router. Router's own
    silent_model dispatch is confirmed broken on /v1/responses and
    Anthropic /v1/messages (silent_model leaks into the underlying provider
    SDK call -- "unexpected keyword argument 'silent_model'") and is
    undocumented/untyped even where it happens to work (chat completions
    only). We never invoke that code path at all: this gateway reads the
    pairing itself and drives both calls through the same
    router.acompletion() surface it already depends on everywhere else.
    """
    pairs: dict[str, str] = {}
    for entry in model_list:
        litellm_params = entry.get("litellm_params") or {}
        silent_model = litellm_params.pop("silent_model", None)
        if silent_model is not None:
            pairs[entry["model_name"]] = silent_model
    return pairs


def load_config(path: str) -> dict[str, Any]:
    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    if "model_list" not in raw:
        print(f"error: {path} has no top-level 'model_list' key", file=sys.stderr)
        sys.exit(1)

    keys_file = os.environ.get("LITELLM_GATEWAY_KEYS_FILE", _DEFAULT_KEYS_FILE)
    provider_keys = _load_provider_keys(keys_file)
    _resolve_provider_keys(raw["model_list"], provider_keys)
    silent_pairs = _extract_silent_pairs(raw["model_list"])

    accepted = _router_accepted_kwargs()

    router_settings: dict[str, Any] = dict(raw.get("router_settings") or {})
    unknown_router_keys = set(router_settings) - accepted
    router_kwargs = {k: v for k, v in router_settings.items() if k in accepted}

    litellm_settings: dict[str, Any] = dict(raw.get("litellm_settings") or {})
    for key in _SUPPORTED_LITELLM_SETTINGS_KEYS & set(litellm_settings):
        setattr(litellm, key, litellm_settings[key])

    ignored_top_level = set(raw) & _KNOWN_UNSUPPORTED_TOP_LEVEL_KEYS
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
        "silent_pairs": silent_pairs,
    }
