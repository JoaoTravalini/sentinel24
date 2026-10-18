from datetime import datetime, timezone
from decimal import Decimal


PROFILE_PARAMETERS = {
    "profile_minimum_samples": 12,
    "profile_minimum_event_days": 3,
    "profile_minimum_observed_hours": 24,
    "profile_amount_multiple": 5,
    "profile_amount_minimum_cents": 100_000,
}

PROFILE_RULES = {
    "PROFILE_AMOUNT_SPIKE": ("transaction_value", 25),
    "DEVICE_NOT_IN_RECENT_PROFILE": ("access_context", 30),
    "IP_NOT_IN_RECENT_PROFILE": ("access_context", 10),
    "CHANNEL_NOT_IN_RECENT_PROFILE": ("access_context", 10),
    "ACCESS_CONTEXT_CHANGE": ("access_context", 35),
    "DESTINATION_NOT_IN_RECENT_PROFILE": ("counterparty", 15),
}


def evaluate_profile(payload: dict, context: dict) -> dict:
    p = PROFILE_PARAMETERS
    profile = context["source_profile"]
    entries = profile["entries"]

    summary = {
        "lookback_days": profile.get("lookback_days"),
        "sample_limit": profile.get("sample_limit"),
        "excluded_recent_minutes": profile.get(
            "excluded_recent_minutes"
        ),
        "sample_count": len(entries),
        "truncated": profile.get("truncated", False),
        "mature": False,
        "maturity_reasons": [],
        "event_days": None,
        "observed_span_seconds": None,
        "median_amount_cents": None,
    }

    signals = []
    result = {"summary": summary, "signals": signals}

    if len(entries) < p["profile_minimum_samples"]:
        summary["maturity_reasons"].append(
            "INSUFFICIENT_SAMPLES"
        )
        return result

    event_days = {
        datetime.fromisoformat(entry["transaction_at"])
        .astimezone(timezone.utc).date()
        for entry in entries
    }

    received = [
        datetime.fromisoformat(entry["received_at"])
        for entry in entries
    ]

    span = (max(received) - min(received)).total_seconds()

    summary["event_days"] = len(event_days)
    summary["observed_span_seconds"] = span

    if len(event_days) < p["profile_minimum_event_days"]:
        summary["maturity_reasons"].append(
            "INSUFFICIENT_EVENT_DAYS"
        )

    if span < p["profile_minimum_observed_hours"] * 3600:
        summary["maturity_reasons"].append(
            "INSUFFICIENT_OBSERVED_SPAN"
        )

    if summary["maturity_reasons"]:
        return result

    summary["mature"] = True

    amounts = sorted(
        entry["amount_cents"] for entry in entries
    )
    size = len(amounts)

    # Representa duas vezes a mediana para comparar usando inteiros.
    median_twice = (
        amounts[(size - 1) // 2] + amounts[size // 2]
    )

    summary["median_amount_cents"] = format(
        Decimal(median_twice) / 2,
        "f",
    )

    def add(code, evidence):
        signals.append({
            "code": code,
            "evidence": evidence,
        })

    if (
        payload["amount"] >= p["profile_amount_minimum_cents"]
        and payload["amount"] * 2
        >= median_twice * p["profile_amount_multiple"]
    ):
        add("PROFILE_AMOUNT_SPIKE", {
            "amount_cents": payload["amount"],
            "median_amount_cents": (
                summary["median_amount_cents"]
            ),
        })

    changed = {}

    for current_field, historical_field, code in (
        (
            "device_id",
            "device_id",
            "DEVICE_NOT_IN_RECENT_PROFILE",
        ),
        (
            "ip",
            "source_ip",
            "IP_NOT_IN_RECENT_PROFILE",
        ),
        (
            "channel",
            "channel",
            "CHANNEL_NOT_IN_RECENT_PROFILE",
        ),
    ):
        known = {
            entry[historical_field]
            for entry in entries
        }

        changed[current_field] = (
            payload["source"][current_field] not in known
        )

        if changed[current_field]:
            add(code, {"sample_count": size})

    if changed["device_id"] and (
        changed["ip"] or changed["channel"]
    ):
        add("ACCESS_CONTEXT_CHANGE", {
            "changed_fields": [
                field
                for field, value in changed.items()
                if value
            ],
        })

    source = (
        payload["source_account"]["owner"]["document_number"]
    )
    destination = (
        payload["destination_account"]["owner"]["document_number"]
    )

    known_destinations = {
        entry["destination_document"]
        for entry in entries
    }

    if (
        source != destination
        and destination not in known_destinations
    ):
        add("DESTINATION_NOT_IN_RECENT_PROFILE", {
            "sample_count": size,
        })

    return result