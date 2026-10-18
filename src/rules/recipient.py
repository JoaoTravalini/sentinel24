from decimal import Decimal


RECIPIENT_PARAMETERS = {
    "recipient_lookback_days": 14,
    "recipient_exclusion_hours": 24,
    "recipient_minimum_days": 3,
    "recipient_peak_multiplier": 2,
    "recipient_prior_payers_5m": 5,
    "recipient_count_5m": 8,
}


RECIPIENT_RULES = {
    "RECIPIENT_FAN_IN": ("network_flow", 25),
}


def evaluate_recipient(
    context: dict,
    parameters: dict | None = None,
) -> dict:
    p = {**RECIPIENT_PARAMETERS, **(parameters or {})}
    clocks = context.get("recipient_profile", {}).get("clocks", {})
    summary = {}
    signals = []

    for clock in ("received", "event"):
        profile = clocks.get(clock, {})
        days = profile.get("daily_peaks", [])
        observed_days = profile.get("observed_days", 0)
        observed_span = profile.get("observed_span_seconds", 0)
        minimum_days = p["recipient_minimum_days"]
        reasons = []

        if len(days) < minimum_days:
            reasons.append("INSUFFICIENT_ACTIVE_DAYS")

        if observed_days < minimum_days:
            reasons.append("INSUFFICIENT_OBSERVED_DAYS")

        if observed_span < (minimum_days - 1) * 24 * 3600:
            reasons.append("INSUFFICIENT_OBSERVED_SPAN")

        mature = not reasons
        limit = p["recipient_count_5m"]
        median = None

        if mature:
            peaks = sorted(day["peak_5m"] for day in days)
            size = len(peaks)
            median_twice = peaks[(size - 1) // 2] + peaks[size // 2]
            median = format(Decimal(median_twice) / 2, "f")
            personalized = (
                median_twice * p["recipient_peak_multiplier"] + 1
            ) // 2
            limit = max(limit, personalized)

        scope = f"destination_{clock}"
        activity = context["activity"][scope]
        count = activity["count_5m"] + 1
        prior_payers = activity["counterparties_5m"]

        summary[clock] = {
            "assessed": clock in clocks,
            "mature": mature,
            "maturity_reasons": reasons,
            "active_days": len(days),
            "observed_days": observed_days,
            "observed_span_seconds": observed_span,
            "median_daily_peak_5m": median,
            "effective_limit_5m": limit,
        }

        if (
            prior_payers >= p["recipient_prior_payers_5m"]
            and count >= limit
        ):
            signals.append({
                "code": "RECIPIENT_FAN_IN",
                "evidence": {
                    "scope": scope,
                    "prior_distinct_payers": prior_payers,
                    "count_including_current": count,
                    "effective_limit": limit,
                    "limit_source": (
                        "historical_profile"
                        if mature
                        else "initial_policy"
                    ),
                },
            })

    return {"summary": summary, "signals": signals}