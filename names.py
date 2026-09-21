#!/usr/bin/env python3
"""Print unique model identifiers from a LiteLLM proxy config YAML.

Usage: ./names.py [path/to/config.yaml]
Defaults to config.yaml in the script's directory.
Prints each identifier once, preserving the order of first appearance.
Identifiers include both `model_name` entries and the value of
`litellm_params.model` when present.
"""

import os
import sys
import yaml


def default_config_path():
    """Return the default config file path (config.yaml in the script directory)."""
    return os.path.join(os.path.dirname(os.path.realpath(__file__)), "config.yaml")


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else default_config_path()

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    seen = set()
    ordered = []

    for entry in config.get("model_list", []):
        if not entry:
            continue
        entry = entry or {}

        name = entry.get("model_name")
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)

        litellm = entry.get("litellm_params") or {}
        model = litellm.get("model")
        if model and model not in seen:
            seen.add(model)
            ordered.append(model)

    for ident in ordered:
        print(ident)


if __name__ == "__main__":
    main()

