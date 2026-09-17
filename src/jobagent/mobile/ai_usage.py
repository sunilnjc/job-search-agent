"""Estimate and aggregate Studio AI burn without invoices, PII, or secrets.

Metering belongs next to provider usage: token counts, model, request type, and
the already-persisted user/job ids on model_runs. Costs are conservative public
list-price estimates, not Stripe/provider invoices. Units are the reserved
budget units from mobile_reserve_ai_usage. Callers must never log resume text,
emails, prompts, or credentials.
"""

from __future__ import annotations

import json
import os
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable, Mapping, Optional
from uuid import UUID

# Conservative published list-price ceilings (USD per 1M tokens). Unknown models
# use the default so averages do not silently undercount. Operators may override
# with MOBILE_AI_PRICE_TABLE JSON; never put API keys in that value.
_DEFAULT_PRICES = (
    ("gpt-4.1-mini", Decimal("0.40"), Decimal("1.60")),
    ("gpt-4.1", Decimal("2.00"), Decimal("8.00")),
    ("gpt-4o-mini", Decimal("0.15"), Decimal("0.60")),
    ("gpt-4o", Decimal("2.50"), Decimal("10.00")),
    ("claude-opus", Decimal("15.00"), Decimal("75.00")),
    ("claude-sonnet", Decimal("3.00"), Decimal("15.00")),
    ("claude-haiku", Decimal("0.80"), Decimal("4.00")),
)
_DEFAULT_UNKNOWN = (Decimal("15.00"), Decimal("75.00"))
_REQUEST_TYPES = {
    "rank": "assessment",
    "rank_job": "assessment",
    "documents": "packet_prepare",
    "prepare_documents": "packet_prepare",
    "prepare": "packet_prepare",
    "chat": "chat",
    "answer_chat": "chat",
}
_PACKET_ASSESSMENT = "assessment"
_PACKET_PREPARE = "packet_prepare"
_TOKEN_MAX = 10_000_000
_UNITS_MAX = 9_000_000_000_000_000
_COST_QUANTUM = Decimal("0.000001")
_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")


def request_type_for(operation: Any) -> Optional[str]:
    if not isinstance(operation, str):
        return None
    return _REQUEST_TYPES.get(operation.strip().lower())


def _nonneg_int(value: Any, maximum: int = _TOKEN_MAX) -> Optional[int]:
    if type(value) is int and 0 <= value <= maximum:
        return value
    return None


def _money(value: Decimal) -> float:
    quantized = value.quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP)
    if quantized < 0:
        quantized = Decimal("0")
    return float(quantized)


def _price_table() -> list[tuple[str, Decimal, Decimal]]:
    raw = os.environ.get("MOBILE_AI_PRICE_TABLE", "").strip()
    if not raw:
        return list(_DEFAULT_PRICES)
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return list(_DEFAULT_PRICES)
    if not isinstance(parsed, dict) or len(parsed) > 32:
        return list(_DEFAULT_PRICES)
    rows = []
    for name, prices in parsed.items():
        if not isinstance(name, str) or not _MODEL_NAME.fullmatch(name) or name.lower().startswith(("sk-", "sb_")):
            return list(_DEFAULT_PRICES)
        if not isinstance(prices, dict):
            return list(_DEFAULT_PRICES)
        try:
            incoming = Decimal(str(prices["input"]))
            outgoing = Decimal(str(prices["output"]))
        except (KeyError, ArithmeticError, ValueError):
            return list(_DEFAULT_PRICES)
        if incoming < 0 or outgoing < 0 or incoming > 1000 or outgoing > 1000:
            return list(_DEFAULT_PRICES)
        rows.append((name.lower(), incoming, outgoing))
    rows.sort(key=lambda item: len(item[0]), reverse=True)
    return rows or list(_DEFAULT_PRICES)


def prices_for_model(model_name: Any) -> tuple[Decimal, Decimal]:
    name = model_name.lower() if isinstance(model_name, str) else ""
    for prefix, incoming, outgoing in _price_table():
        if name.startswith(prefix):
            return incoming, outgoing
    return _DEFAULT_UNKNOWN


def estimate_cost_usd(model_name: Any, input_tokens: Any, output_tokens: Any) -> Optional[float]:
    incoming = _nonneg_int(input_tokens)
    outgoing = _nonneg_int(output_tokens)
    if incoming is None or outgoing is None:
        return None
    input_rate, output_rate = prices_for_model(model_name)
    million = Decimal("1000000")
    cost = (Decimal(incoming) * input_rate + Decimal(outgoing) * output_rate) / million
    return _money(cost)


def usage_record(
    metadata: Optional[Mapping[str, Any]] = None,
    *,
    request_type: Any = None,
    operation: Any = None,
    reserved_units: Any = None,
    reservation_id: Any = None,
) -> dict:
    """Bounded usage object suitable for model_runs.output_summary.

    Omits unknown keys, prompts, and identities. user_id/job_id stay on the row.
    """
    raw = dict(metadata or {})
    kind = request_type_for(request_type) or request_type_for(raw.get("request_type"))
    kind = kind or request_type_for(operation)
    if kind is None and isinstance(request_type, str) and request_type in {
        "assessment", "packet_prepare", "chat",
    }:
        kind = request_type
    incoming = _nonneg_int(raw.get("input_tokens"))
    outgoing = _nonneg_int(raw.get("output_tokens"))
    model = raw.get("model_name")
    if not isinstance(model, str) or not _MODEL_NAME.fullmatch(model) or model.startswith(("sk-", "sb_")):
        model = None
    record: dict[str, Any] = {}
    if kind:
        record["request_type"] = kind
    if incoming is not None:
        record["input_tokens"] = incoming
    if outgoing is not None:
        record["output_tokens"] = outgoing
    existing = raw.get("estimated_cost_usd")
    if type(existing) in (int, float) and 0 <= existing <= 1000:
        record["estimated_cost_usd"] = _money(Decimal(str(existing)))
        record["cost_basis"] = raw.get("cost_basis") if raw.get("cost_basis") == "public_list_price_estimate" else "public_list_price_estimate"
    else:
        cost = estimate_cost_usd(model or raw.get("model_name"), incoming, outgoing)
        if cost is not None:
            record["estimated_cost_usd"] = cost
            record["cost_basis"] = "public_list_price_estimate"
    units = _nonneg_int(reserved_units, _UNITS_MAX)
    if units is not None:
        record["reserved_units"] = units
    if isinstance(reservation_id, str):
        try:
            record["reservation_id"] = str(UUID(reservation_id))
        except (ValueError, TypeError, AttributeError):
            pass
    return record


def _usage_from_run(run: Mapping[str, Any]) -> dict:
    summary = run.get("output_summary")
    if not isinstance(summary, dict):
        return {}
    usage = summary.get("usage")
    if isinstance(usage, dict):
        return usage_record(
            usage,
            request_type=usage.get("request_type"),
            operation=run.get("operation"),
            reserved_units=usage.get("reserved_units"),
            reservation_id=usage.get("reservation_id"),
        )
    metadata = summary.get("model_metadata")
    return usage_record(metadata if isinstance(metadata, dict) else {}, operation=run.get("operation"))


def _add_usage(total: dict, usage: Mapping[str, Any]) -> None:
    for key in ("input_tokens", "output_tokens", "reserved_units"):
        value = _nonneg_int(usage.get(key), _UNITS_MAX if key == "reserved_units" else _TOKEN_MAX)
        if value is None:
            continue
        total[key] = total.get(key, 0) + value
        total[f"{key}_known"] = True
    cost = usage.get("estimated_cost_usd")
    if type(cost) in (int, float) and cost >= 0 and cost <= 1000:
        total["estimated_cost_usd"] = _money(Decimal(str(total.get("estimated_cost_usd", 0))) + Decimal(str(cost)))
        total["estimated_cost_usd_known"] = True


def _finalize_usage(total: dict) -> dict:
    result = {}
    for key in ("input_tokens", "output_tokens", "reserved_units"):
        if total.get(f"{key}_known"):
            result[key] = int(total[key])
    if total.get("estimated_cost_usd_known"):
        result["estimated_cost_usd"] = float(total["estimated_cost_usd"])
        result["cost_basis"] = "public_list_price_estimate"
    return result


def packet_burn_from_runs(runs: Iterable[Mapping[str, Any]]) -> list[dict]:
    """One completed packet is a succeeded prepare plus the latest prior assessment.

    Assessment = rank_job. Prepare = prepare_documents (resume + cover letter).
    Incomplete packets (prepare without a metered assessment) are retained with
    incomplete=true so operators can see coverage, not silently dropped.
    """
    prepared: list[dict] = []
    assessments: list[dict] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        operation = run.get("operation")
        status = run.get("status")
        if status != "succeeded":
            continue
        item = {
            "id": run.get("id"),
            "user_id": run.get("user_id"),
            "job_id": run.get("job_id"),
            "completed_at": run.get("completed_at") or "",
            "usage": _usage_from_run(run),
        }
        if operation == "prepare_documents":
            prepared.append(item)
        elif operation == "rank_job":
            assessments.append(item)
    assessments.sort(key=lambda row: row["completed_at"])
    packets = []
    for prepare in sorted(prepared, key=lambda row: row["completed_at"]):
        match = None
        for assessment in assessments:
            if assessment["user_id"] != prepare["user_id"] or assessment["job_id"] != prepare["job_id"]:
                continue
            if assessment["completed_at"] <= (prepare["completed_at"] or ""):
                match = assessment
        total: dict[str, Any] = {}
        _add_usage(total, prepare.get("usage") or {})
        if match:
            _add_usage(total, match.get("usage") or {})
        packet = {
            "prepare_run_id": prepare["id"],
            "assessment_run_id": match["id"] if match else None,
            "incomplete": match is None,
            **_finalize_usage(total),
        }
        packets.append(packet)
    return packets


def summarize_packet_burn(packets: Iterable[Mapping[str, Any]]) -> dict:
    complete = [dict(packet) for packet in packets if not packet.get("incomplete")]
    metered = [packet for packet in complete if "estimated_cost_usd" in packet and "reserved_units" in packet]
    summary = {
        "completed_packets": len(complete),
        "incomplete_packets": sum(1 for packet in packets if packet.get("incomplete")),
        "metered_packets": len(metered),
        "mean_estimated_cost_usd": None,
        "mean_reserved_units": None,
        "cost_basis": "public_list_price_estimate",
    }
    if metered:
        summary["mean_estimated_cost_usd"] = _money(
            sum((Decimal(str(packet["estimated_cost_usd"])) for packet in metered), Decimal("0"))
            / Decimal(len(metered))
        )
        summary["mean_reserved_units"] = float(
            sum(packet["reserved_units"] for packet in metered) / len(metered)
        )
    return summary


def attach_packet_burn(prepare_run: Mapping[str, Any], assessment_run: Optional[Mapping[str, Any]]) -> dict:
    packets = packet_burn_from_runs([run for run in (assessment_run, prepare_run) if run])
    return packets[0] if packets else {"prepare_run_id": prepare_run.get("id"), "incomplete": True}
