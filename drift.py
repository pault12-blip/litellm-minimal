"""
Drift ledger for the minimal gateway -- no database, no litellm.Router
silent_model dispatch (confirmed broken on /v1/responses and Anthropic
/v1/messages upstream; see config.py for details). This gateway reads the
primary->silent pairing from config.yaml itself, strips silent_model before
Router ever sees it, and drives both calls itself from app.py through the
same router.acompletion() surface used everywhere else in this project.

Both calls carry a shared drift_correlation_id (injected by app.py) in
metadata, plus a drift_role of "primary" or "silent". custom_callbacks.py
routes any call carrying these to record_half() below.

Correlation is join-by-file, not memory+TTL, with ONE FILE PER ROLE per
correlation_id: DRIFT/pending/{correlation_id}.primary.json and
DRIFT/pending/{correlation_id}.silent.json. This matters because Router's
own internal retries/fallbacks fire this callback multiple times for the
SAME logical request, all sharing the same correlation_id and the same
role -- a naive single shared pending file would (and did, in testing)
mistake a same-role retry for the other side's half, splicing together two
unrelated attempts of the SAME role into one bogus "pair". Writing to our
own role's file is always just an overwrite (the latest attempt for that
role wins); a pair is only ever finalized once BOTH distinct role files
exist. An orphan (one side never reports at all) is just a leftover file on
disk, inspectable and cleanable by hand -- never a silent memory leak and
never a TTL guess.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

DRIFT_DIR = os.environ.get("DRIFT_DIR", "DRIFT")
PENDING_DIR = os.path.join(DRIFT_DIR, "pending")
LEDGER_FILE = os.path.join(DRIFT_DIR, "ledger.jsonl")


def _ensure_dirs() -> None:
    os.makedirs(PENDING_DIR, exist_ok=True)


def _pending_path(correlation_id: str, role: str) -> str:
    return os.path.join(PENDING_DIR, f"{correlation_id}.{role}.json")


def _response_text(response_obj: Any) -> str | None:
    """Best-effort extraction of the assistant text from a ModelResponse."""
    try:
        return response_obj.choices[0].message.content
    except Exception:
        return None


def record_half(
    correlation_id: str,
    role: str,  # "primary" or "silent"
    model_name: str,
    provider: str,
    prompt: Any,
    response_obj: Any,
    error: Exception | None,
) -> None:
    _ensure_dirs()
    other_role = "silent" if role == "primary" else "primary"
    half = {
        "role": role,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "prompt": prompt,
        "model_name": model_name,
        "provider": provider,
        "response": None if error else _response_text(response_obj),
        "error": str(error) if error else None,
    }

    own_path = _pending_path(correlation_id, role)
    other_path = _pending_path(correlation_id, other_role)

    if not os.path.exists(other_path):
        # Either the first side to arrive, or a retry of OUR OWN role.
        # Either way: just (over)write our own role's file with this
        # latest outcome. This is what stops a same-role retry from ever
        # being mistaken for the other side's half.
        tmp_path = own_path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(half, f)
        os.replace(tmp_path, own_path)
        return

    # The other role's file already exists: this really is the completing
    # half. Merge, append to the ledger, clean up both files.
    try:
        with open(other_path) as f:
            other = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[DRIFT] warning: pending file {other_path} unreadable ({e}); dropping this half")
        return

    primary = half if role == "primary" else other
    silent = half if role == "silent" else other

    combined = {
        "ts": primary["ts"],
        "prompt": primary["prompt"],
        "model_name": primary["model_name"],
        "provider": primary["provider"],
        "response": primary["response"],
        "silent_name": silent["model_name"],
        "silent_provider": silent["provider"],
        "silent_response": silent["response"],
    }
    if primary["error"]:
        combined["error"] = primary["error"]
    if silent["error"]:
        combined["silent_error"] = silent["error"]

    with open(LEDGER_FILE, "a") as f:
        f.write(json.dumps(combined) + "\n")

    os.remove(other_path)
    if os.path.exists(own_path):
        os.remove(own_path)

