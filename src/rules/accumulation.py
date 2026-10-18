from decimal import Decimal


ACCUMULATION_PARAMETERS = {
    "accumulation_lookback_days": 14,
    "accumulation_exclusion_hours": 24,
    "accumulation_minimum_days": 3,
    "accumulation_minimum_samples": 12,
    "accumulation_minimum_transactions": 3,
    "accumulation_minimum_cents": 100_000,
    "accumulation_multiplier": 2,
}

ACCUMULATION_RULES = {
    "ACCUMULATED_AMOUNT_1H": ("velocity", 35),
    "ACCUMULATED_AMOUNT_24H": ("velocity", 35),
}


def _same_owner(payload: dict) -> bool:
    return (
        payload["source_account"]["owner"]["document_number"]
        == payload["destination_account"]["owner"]["document_number"]
    )


def _windows(payload: dict, context: dict) -> dict:
    activity = context["activity"]["source_received"]
    result = {}

    for window in ("1h", "24h"):
        prefix = "approved_third_party"

        count = activity[f"{prefix}_count_{window}"] + 1
        total = (
            activity[f"{prefix}_amount_cents_{window}"]
            + payload["amount"]
        )
        largest = max(
            activity[f"{prefix}_max_cents_{window}"],
            payload["amount"],
        )

        result[window] = {
            "count_including_current": count,
            "total_cents": total,
            "largest_cents": largest,
            "adjusted_cents": total - largest,
        }

    return result


def needs_accumulation_reference(
    payload: dict, context: dict
) -> bool:
    if _same_owner(payload):
        return False

    p = ACCUMULATION_PARAMETERS

    return any(
        item["count_including_current"]
        >= p["accumulation_minimum_transactions"]
        and item["adjusted_cents"]
        >= p["accumulation_minimum_cents"]
        for item in _windows(payload, context).values()
    )


def evaluate_accumulation(payload: dict, context: dict) -> dict:
    p = ACCUMULATION_PARAMETERS
    profile = context.get("amount_profile", {})

    summary = {
        "assessed": profile.get("assessed", False),
        "mature": False,
        "clock": "server_observed_at",
        "scope": "source_owner_to_other_owners",
        "adjustment": "exclude_largest_transaction",
        "maturity_reasons": [],
        "windows": {},
    }
    result = {"summary": summary, "signals": []}

    if _same_owner(payload):
        summary["reason"] = "SAME_OWNER"
        return result

    windows = _windows(payload, context)
    summary["windows"] = windows

    days = profile.get("daily_peaks", [])
    count = profile.get("sample_count", 0)
    span = profile.get("observed_span_seconds", 0)
    minimum_days = p["accumulation_minimum_days"]

    summary.update(
        sample_count=count,
        active_days=len(days),
        observed_span_seconds=span,
    )

    for insufficient, reason in (
        (
            count < p["accumulation_minimum_samples"],
            "INSUFFICIENT_SAMPLES",
        ),
        (
            len(days) < minimum_days,
            "INSUFFICIENT_ACTIVE_DAYS",
        ),
        (
            span < (minimum_days - 1) * 86400,
            "INSUFFICIENT_OBSERVED_SPAN",
        ),
    ):
        if insufficient:
            summary["maturity_reasons"].append(reason)

    if summary["maturity_reasons"]:
        return result

    summary["mature"] = True

    for window, current in windows.items():
        peaks = sorted(
            day[f"peak_adjusted_cents_{window}"]
            for day in days
        )
        size = len(peaks)

        median_twice = (
            peaks[(size - 1) // 2] + peaks[size // 2]
        )
        personalized = (
            median_twice * p["accumulation_multiplier"] + 1
        ) // 2
        limit = max(
            p["accumulation_minimum_cents"],
            personalized,
        )

        current["median_daily_peak_cents"] = format(
            Decimal(median_twice) / 2, "f"
        )
        current["effective_limit_cents"] = limit

        if (
            current["count_including_current"]
            >= p["accumulation_minimum_transactions"]
            and current["adjusted_cents"] >= limit
        ):
            result["signals"].append({
                "code": f"ACCUMULATED_AMOUNT_{window.upper()}",
                "evidence": dict(current),
            })

    return result