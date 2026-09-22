"""
FOCUS 1.2 export + checkpoint reconciliation for the minimal gateway.

The FOCUS column schema/order below is the external FinOps FOCUS 1.2
specification (https://focus.finops.org/focus-specification/v1-2/), not
litellm's own code -- that's the frozen protocol this file depends on.
litellm's own litellm/integrations/focus/ implementation is not imported or
depended on: it requires polars and a live Prisma-backed proxy DB, neither
of which exist in this gateway.

Two flat files, no DB:
  - costs_ledger.tsv: append-only, one line per call, written by
    custom_callbacks.py: "timestamp provider model spend"
  - checkpoints.tsv: append-only, one line per manually-recorded real
    billing figure: "timestamp provider cumulative_billed_cost"

correction_factor per provider = (actual delta between the last two
checkpoints) / (sum of ledger spend in that same interval). EffectiveCost
in the export is always raw ledger spend, untouched. BilledCost is
spend * correction_factor for that provider, applied forward until the next
checkpoint updates the factor.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple


class LedgerRow(NamedTuple):
    ts: datetime
    provider: str
    model: str
    spend: float


class CheckpointRow(NamedTuple):
    ts: datetime
    provider: str
    cumulative_billed_cost: float


def _parse_ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _format_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_ledger(path: str, provider: str, model: str, spend: float) -> None:
    ts = _format_ts(datetime.now(timezone.utc))
    with open(path, "a") as f:
        f.write(f"{ts} {provider} {model} {spend:.6f}\n")


def read_ledger(path: str) -> list[LedgerRow]:
    rows: list[LedgerRow] = []
    try:
        with open(path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return rows

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        fields = stripped.split(" ")
        if len(fields) != 4:
            print(f"warning: costs_ledger.tsv: skipping malformed line: {line!r}")
            continue
        try:
            rows.append(
                LedgerRow(
                    ts=_parse_ts(fields[0]),
                    provider=fields[1],
                    model=fields[2],
                    spend=float(fields[3]),
                )
            )
        except ValueError:
            print(f"warning: costs_ledger.tsv: skipping malformed line: {line!r}")
    return rows


def read_checkpoints(path: str) -> dict[str, list[CheckpointRow]]:
    by_provider: dict[str, list[CheckpointRow]] = {}
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
            print(f"warning: checkpoints.tsv: skipping malformed line: {line!r}")
            continue
        try:
            row = CheckpointRow(
                ts=_parse_ts(fields[0]),
                provider=fields[1],
                cumulative_billed_cost=float(fields[2]),
            )
        except ValueError:
            print(f"warning: checkpoints.tsv: skipping malformed line: {line!r}")
            continue
        by_provider.setdefault(row.provider, []).append(row)

    for provider, rows in by_provider.items():
        rows.sort(key=lambda r: r.ts)

    return by_provider


def correction_factor(provider: str, checkpoints: dict[str, list[CheckpointRow]], ledger: list[LedgerRow]) -> float:
    rows = checkpoints.get(provider)
    if rows is None or len(rows) < 2:
        return 1.0

    a, b = rows[-2], rows[-1]
    actual_delta = b.cumulative_billed_cost - a.cumulative_billed_cost
    estimated_delta = sum(r.spend for r in ledger if r.provider == provider and a.ts < r.ts <= b.ts)

    if estimated_delta == 0:
        return 1.0
    return actual_delta / estimated_delta


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
        "ConsumedQuantity": "1.000000",
        "ConsumedUnit": "Requests",
        "ContractedCost": f"{row.spend:.6f}",
        "ContractedUnitPrice": None,
        "EffectiveCost": f"{row.spend:.6f}",
        "InvoiceIssuerName": row.provider,
        "ListCost": f"{row.spend:.6f}",
        "ListUnitPrice": None,
        "PricingCategory": None,
        "PricingQuantity": "1.000000",
        "PricingUnit": "Requests",
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


def export_focus_csv(ledger_path: str, checkpoints_path: str, out_path: str) -> int:
    """Writes the FOCUS 1.2 CSV export. Returns the number of rows written."""
    ledger = read_ledger(ledger_path)
    checkpoints = read_checkpoints(checkpoints_path)
    factors = {provider: correction_factor(provider, checkpoints, ledger) for provider in {r.provider for r in ledger}}

    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_FOCUS_COLUMNS)
        writer.writeheader()
        for row in ledger:
            writer.writerow(_focus_row(row, factors.get(row.provider, 1.0)))
    import os
    os.replace(tmp_path, out_path)
    return len(ledger)

