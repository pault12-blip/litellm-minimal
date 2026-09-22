"""
FOCUS 1.2 export + checkpoint reconciliation for the minimal gateway.

Runs as a STANDALONE script (cron/systemd timer/manual), separate from the
gateway process -- the gateway only ever appends to the ledger via
custom_callbacks.py. Run this file directly to reconcile + export:

    python3 focus.py

The FOCUS column schema/order below is the external FinOps FOCUS 1.2
specification (https://focus.finops.org/focus-specification/v1-2/), not
litellm's own code -- that's the frozen protocol this file depends on.
litellm's own litellm/integrations/focus/ implementation is not imported or
depended on: it requires polars and a live Prisma-backed proxy DB, neither
of which exist in this gateway.

Files, all under FOCUS_DIR (default "FOCUS/"), no DB:
  - costs_ledger.tsv: append-only, one line per call, written by
    custom_callbacks.py: "timestamp provider model spend n_tokens"
  - checkpoint.tab: current manual snapshot of provider -> remaining
    account balance, as shown in the provider's own billing UI: one line
    per provider, "timestamp provider balance". You edit/replace this file
    by hand whenever you look at a provider's billing page.
  - checkpoint.old.tab: the previous snapshot, same shape. Produced by
    rotating checkpoint.tab -> checkpoint.old.tab after each reconciliation.
  - correction_factors.tab: the last computed factor per provider, one
    line "provider factor". Persists the factor across script runs so it
    keeps applying forward to new ledger rows until the next checkpoint
    closes a new interval.
  - focus_export.csv: the FOCUS 1.2 export. APPEND-ONLY: each run only
    writes rows for ledger lines not yet exported, priced with whatever
    correction factor is current at that moment. A row already written is
    never rewritten, even if a later checkpoint changes the factor -- that
    is what "apply forward only" actually means in an append-only file.
  - export_position.tab: how many ledger lines have been exported so far;
    tracks the append-only boundary above.

Because balance DECREASES as you spend (that's what every hyperscaler's
billing UI actually exposes), actual_delta = old_balance - new_balance,
the inverse of a cumulative-billed-cost counter. correction_factor per
provider = actual_delta / (sum of ledger spend in that same interval).
EffectiveCost in the export is always raw ledger spend, untouched.
BilledCost is spend * correction_factor for that provider.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple

FOCUS_DIR = os.environ.get("FOCUS_DIR", "FOCUS")
LEDGER_FILE = os.path.join(FOCUS_DIR, "costs_ledger.tsv")
CHECKPOINT_FILE = os.path.join(FOCUS_DIR, "checkpoint.tab")
CHECKPOINT_OLD_FILE = os.path.join(FOCUS_DIR, "checkpoint.old.tab")
FACTORS_FILE = os.path.join(FOCUS_DIR, "correction_factors.tab")
EXPORT_FILE = os.path.join(FOCUS_DIR, "focus_export.csv")
EXPORT_POSITION_FILE = os.path.join(FOCUS_DIR, "export_position.tab")


class LedgerRow(NamedTuple):
    ts: datetime
    provider: str
    model: str
    spend: float
    n_tokens: int


class BalanceRow(NamedTuple):
    ts: datetime
    provider: str
    balance: float


def _parse_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _format_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_focus_dir() -> None:
    os.makedirs(FOCUS_DIR, exist_ok=True)


def append_ledger(provider: str, model: str, spend: float, n_tokens: int) -> None:
    ensure_focus_dir()
    ts = _format_ts(datetime.now(timezone.utc))
    with open(LEDGER_FILE, "a") as f:
        f.write(f"{ts} {provider} {model} {spend:.6f} {n_tokens}\n")


def read_ledger() -> list[LedgerRow]:
    rows: list[LedgerRow] = []
    try:
        with open(LEDGER_FILE) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return rows

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        fields = stripped.split(" ")
        if len(fields) != 5:
            print(f"warning: costs_ledger.tsv: skipping malformed line: {line!r}")
            continue
        try:
            rows.append(
                LedgerRow(
                    ts=_parse_ts(fields[0]),
                    provider=fields[1],
                    model=fields[2],
                    spend=float(fields[3]),
                    n_tokens=int(fields[4]),
                )
            )
        except ValueError:
            print(f"warning: costs_ledger.tsv: skipping malformed line: {line!r}")
    return rows


def read_balance_file(path: str) -> dict[str, BalanceRow]:
    """One current balance per provider. If a provider appears more than
    once, the last line wins (you overwrote/appended a newer reading)."""
    by_provider: dict[str, BalanceRow] = {}
    try:
        with open(path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return by_provider

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split(" ")
        if len(fields) != 3:
            print(f"warning: {path}: skipping malformed line: {line!r}")
            continue
        try:
            row = BalanceRow(ts=_parse_ts(fields[0]), provider=fields[1], balance=float(fields[2]))
        except ValueError:
            print(f"warning: {path}: skipping malformed line: {line!r}")
            continue
        by_provider[row.provider] = row
    return by_provider


def read_factors_file(path: str) -> dict[str, float]:
    factors: dict[str, float] = {}
    try:
        with open(path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return factors
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        fields = stripped.split(" ")
        if len(fields) != 2:
            print(f"warning: {path}: skipping malformed line: {line!r}")
            continue
        try:
            factors[fields[0]] = float(fields[1])
        except ValueError:
            print(f"warning: {path}: skipping malformed line: {line!r}")
    return factors


def write_factors_file(path: str, factors: dict[str, float]) -> None:
    ensure_focus_dir()
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        for provider, factor in sorted(factors.items()):
            f.write(f"{provider} {factor:.6f}\n")
    os.replace(tmp_path, path)


def compute_correction_factors(
    old_balances: dict[str, BalanceRow],
    new_balances: dict[str, BalanceRow],
    ledger: list[LedgerRow],
) -> dict[str, float]:
    """One factor per provider present in the NEW balance file. A provider
    with no prior (old) reading yet gets 1.0 -- there's nothing to compare
    against on its first-ever checkpoint."""
    factors: dict[str, float] = {}
    for provider, new_row in new_balances.items():
        old_row = old_balances.get(provider)
        if old_row is None:
            factors[provider] = 1.0
            continue
        actual_delta = old_row.balance - new_row.balance
        estimated_delta = sum(
            r.spend for r in ledger if r.provider == provider and old_row.ts < r.ts <= new_row.ts
        )
        factors[provider] = 1.0 if estimated_delta == 0 else actual_delta / estimated_delta
    return factors


_FOCUS_COLUMNS = [
    "BilledCost", "BillingAccountId", "BillingAccountName", "BillingCurrency",
    "BillingPeriodStart", "BillingPeriodEnd", "ChargeCategory", "ChargeClass",
    "ChargeDescription", "ChargeFrequency", "ChargePeriodStart", "ChargePeriodEnd",
    "ConsumedQuantity", "ConsumedUnit", "ContractedCost", "ContractedUnitPrice",
    "EffectiveCost", "InvoiceIssuerName", "ListCost", "ListUnitPrice",
    "PricingCategory", "PricingQuantity", "PricingUnit", "ProviderName",
    "PublisherName", "RegionId", "RegionName", "ResourceId", "ResourceName",
    "ResourceType", "ServiceCategory", "ServiceSubcategory", "ServiceName",
    "SubAccountId", "SubAccountName", "SubAccountType", "Tags",
]


def _focus_row(row: LedgerRow, factor: float) -> dict[str, Any]:
    period_start = row.ts.replace(hour=0, minute=0, second=0, microsecond=0)
    period_end = period_start + timedelta(days=1)
    billed = row.spend * factor
    return {
        "BilledCost": f"{billed:.6f}",
        "BillingAccountId": None,
        "BillingAccountName": None,
        "BillingCurrency": "USD",
        "BillingPeriodStart": _format_ts(period_start),
        "BillingPeriodEnd": _format_ts(period_end),
        "ChargeCategory": "Usage",
        "ChargeClass": None,
        "ChargeDescription": row.model,
        "ChargeFrequency": "Usage-Based",
        "ChargePeriodStart": _format_ts(period_start),
        "ChargePeriodEnd": _format_ts(period_end),
        "ConsumedQuantity": str(row.n_tokens),
        "ConsumedUnit": "Tokens",
        "ContractedCost": f"{row.spend:.6f}",
        "ContractedUnitPrice": None,
        "EffectiveCost": f"{row.spend:.6f}",
        "InvoiceIssuerName": row.provider,
        "ListCost": f"{row.spend:.6f}",
        "ListUnitPrice": None,
        "PricingCategory": None,
        "PricingQuantity": str(row.n_tokens),
        "PricingUnit": "Tokens",
        "ProviderName": row.provider,
        "PublisherName": row.provider,
        "RegionId": None,
        "RegionName": None,
        "ResourceId": row.model,
        "ResourceName": row.model,
        "ResourceType": row.model,
        "ServiceCategory": "AI and Machine Learning",
        "ServiceSubcategory": "Generative AI",
        "ServiceName": row.model,
        "SubAccountId": None,
        "SubAccountName": None,
        "SubAccountType": None,
        "Tags": "{}",
    }


def read_export_position() -> int:
    try:
        with open(EXPORT_POSITION_FILE) as f:
            return int(f.read().strip() or 0)
    except (FileNotFoundError, ValueError):
        return 0


def write_export_position(n: int) -> None:
    ensure_focus_dir()
    tmp_path = EXPORT_POSITION_FILE + ".tmp"
    with open(tmp_path, "w") as f:
        f.write(str(n) + "\n")
    os.replace(tmp_path, EXPORT_POSITION_FILE)


def export_focus_csv(ledger: list[LedgerRow], factors: dict[str, float], out_path: str) -> int:
    """Append-only: only ledger rows not yet exported are written, each
    priced with whatever factor is CURRENT right now. A row, once
    exported, is never touched again on a later run -- that's what makes
    "apply forward only, never rewrite historical rows" actually true,
    even though the whole ledger is re-read and re-priced-in-principle
    every run.
    """
    position = read_export_position()
    new_rows = ledger[position:]
    if not new_rows:
        return 0

    ensure_focus_dir()
    file_is_new = not os.path.exists(out_path)
    with open(out_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_FOCUS_COLUMNS)
        if file_is_new:
            writer.writeheader()
        for row in new_rows:
            writer.writerow(_focus_row(row, factors.get(row.provider, 1.0)))

    write_export_position(len(ledger))
    return len(new_rows)


def reconcile_and_export() -> None:
    ledger = read_ledger()

    if os.path.exists(CHECKPOINT_FILE):
        old_balances = read_balance_file(CHECKPOINT_OLD_FILE)
        new_balances = read_balance_file(CHECKPOINT_FILE)
        factors = compute_correction_factors(old_balances, new_balances, ledger)
        # keep any previously-known factors for providers not in this
        # checkpoint round, so a provider you don't check every time
        # doesn't silently fall back to 1.0
        merged = {**read_factors_file(FACTORS_FILE), **factors}
        write_factors_file(FACTORS_FILE, merged)
        n = export_focus_csv(ledger, merged, EXPORT_FILE)
        os.replace(CHECKPOINT_FILE, CHECKPOINT_OLD_FILE)
        print(f"[FOCUS] reconciled {sorted(factors)}, appended {n} new rows, rotated checkpoint.tab")
    else:
        factors = read_factors_file(FACTORS_FILE)
        n = export_focus_csv(ledger, factors, EXPORT_FILE)
        print(f"[FOCUS] no new checkpoint.tab; appended {n} new rows using last known factors")


if __name__ == "__main__":
    reconcile_and_export()

