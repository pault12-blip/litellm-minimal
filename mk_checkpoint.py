#!/usr/bin/env python3
import sys
from datetime import datetime, timezone

def main():
    master = "checkpoint.master"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with open(master) as f:
        for line in f:
            parts = line.split()
            if len(parts) < 2:
                continue
            name, value = parts[0], parts[1]
            print(f"{ts} {name} {value}")

if __name__ == "__main__":
    main()
