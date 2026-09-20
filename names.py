#!/usr/bin/env python3
"""Print all callable model_name entries from a litellm proxy config YAML.

Usage: ./names.py [path/to/config.yaml]
Defaults to config.yaml in the script's own directory.
Prints each unique model_name once, in first-seen order.
"""
import os
import sys
import yaml


def main():
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        script_dir = os.path.dirname(os.path.realpath(__file__))
        path = os.path.join(script_dir, "config.yaml")

    with open(path, "r") as f:
        config = yaml.safe_load(f)

    seen = []
    for entry in config.get("model_list", []):
        name = entry.get("model_name")
        if name and name not in seen:
            seen.append(name)

    for name in seen:
        print(name)


if __name__ == "__main__":
    main()

