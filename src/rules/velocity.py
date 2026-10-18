from decimal import Decimal


FREQUENCY_PARAMETERS = {
    "frequency_lookback_days": 14,
    "frequency_exclusion_hours": 24,
    "frequency_minimum_days": 3,
    "frequency_multiplier": 2,
    "burst_1m_count": 8,
    "burst_5m_count": 20,
}


def evaluate_velocity(
    context: dict,
    parameters: dict | None = None,
) -> dict:
    p = {**FREQUENCY_PARAMETERS, **(parameters or {})}
    history = context.get("frequency_profile", {}).get("clocks", {})
    summary = {}
    signals = []

    for clock in ("received", "event"):
        profile = history.get(clock, {})
        days = profile.get("daily_peaks", [])
        observed_days = profile.get("observed_days", 0)
        observed_span = profile.get("observed_span_seconds", 0)

        minimum_days = p["frequency_minimum_days"]
        minimum_span = (minimum_days - 1) * 24 * 3600
        reasons = []

        if len(days) < minimum_days:
            reasons.append("INSUFFICIENT_ACTIVE_DAYS")

        if observed_days < minimum_days:
            reasons.append("INSUFFICIENT_OBSERVED_DAYS")

        if observed_span < minimum_span:
            reasons.append("INSUFFICIENT_OBSERVED_SPAN")

        mature = not reasons
        limits = {}
        medians = {}

        for window in ("1m", "5m"):
            limit = p[f"burst_{window}_count"]
            medians[window] = None

            if mature:
                peaks = sorted(
                    day[f"peak_{window}"]
                    for day in days
                )
                size = len(peaks)

                # Duas vezes a mediana, preservando valores como 8,5.
                median_twice = (
                    peaks[(size - 1) // 2]
                    + peaks[size // 2]
                )

                medians[window] = format(
                    Decimal(median_twice) / 2,
                    "f",
                )

                # Arredonda para cima o limite personalizado.
                personalized = (
                    median_twice * p["frequency_multiplier"] + 1
                ) // 2

                limit = max(limit, personalized)

            limits[window] = limit
            scope = f"source_{clock}"

            # O histórico não contém a solicitação atual.
            current_count = (
                context["activity"][scope][f"count_{window}"] + 1
            )

            if current_count >= limit:
                signals.append({
                    "code": f"SOURCE_BURST_{window.upper()}",
                    "evidence": {
                        "scope": scope,
                        "count_including_current": current_count,
                        "effective_limit": limit,
                        "limit_source": (
                            "historical_profile"
                            if mature
                            else "initial_policy"
                        ),
                    },
                })

        summary[clock] = {
            "mature": mature,
            "maturity_reasons": reasons,
            "active_days": len(days),
            "observed_days": observed_days,
            "observed_span_seconds": observed_span,
            "median_daily_peaks": medians,
            "effective_limits": limits,
        }

    return {
        "summary": summary,
        "signals": signals,
    }