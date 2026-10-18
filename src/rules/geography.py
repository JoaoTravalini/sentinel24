from datetime import datetime
from math import asin, cos, radians, sin, sqrt


# Margens de política; não representam precisão medida do GPS.
GEOGRAPHY_PARAMETERS = {
    "geo_lookback_hours": 24,
    "geo_distance_tolerance_km": 30,
    "geo_time_tolerance_seconds": 120,
    "geo_maximum_speed_kmh": 1000,
}

GEOGRAPHY_RULES = {
    "IMPLAUSIBLE_TRAVEL": ("access_context", 35),
}


def distance_km(first: dict, second: dict) -> float:
    lat1 = radians(first["latitude"])
    lat2 = radians(second["latitude"])

    delta_lat = lat2 - lat1
    delta_lon = radians(
        second["longitude"] - first["longitude"]
    )

    value = (
        sin(delta_lat / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    )

    return (
        2
        * 6371.0088
        * asin(sqrt(min(1.0, max(0.0, value))))
    )


def evaluate_geography(payload: dict, context: dict) -> dict:
    p = GEOGRAPHY_PARAMETERS
    summary = {"assessed": False}
    result = {"summary": summary, "signals": []}

    current = payload.get("source", {}).get("geolocation")
    previous = context.get("previous_location")

    if current is None:
        summary["reason"] = "CURRENT_LOCATION_MISSING"
        return result

    if (
        previous is None
        or previous.get("geolocation") is None
    ):
        summary["reason"] = "PREVIOUS_LOCATION_MISSING"
        return result

    if (
        previous["device_id"]
        != payload["source"]["device_id"]
    ):
        summary["reason"] = "DIFFERENT_DEVICE"
        return result

    event_at = datetime.fromisoformat(
        payload["transaction_date"]
    )
    previous_at = datetime.fromisoformat(
        previous["transaction_at"]
    )
    elapsed = (event_at - previous_at).total_seconds()

    if (
        elapsed < 0
        or elapsed > p["geo_lookback_hours"] * 3600
    ):
        summary["reason"] = "OUTSIDE_TIME_WINDOW"
        return result

    distance = distance_km(
        previous["geolocation"],
        current,
    )

    effective_distance = max(
        0,
        distance - p["geo_distance_tolerance_km"],
    )
    effective_seconds = (
        elapsed + p["geo_time_tolerance_seconds"]
    )

    speed = (
        effective_distance * 3600 / effective_seconds
        if effective_seconds > 0
        else None
    )

    allowed_distance = (
        p["geo_distance_tolerance_km"]
        + p["geo_maximum_speed_kmh"]
        * effective_seconds
        / 3600
    )

    suspicious = distance > allowed_distance

    summary.update({
        "assessed": True,
        "reason": (
            "TRAVEL_ENVELOPE_EXCEEDED"
            if suspicious
            else "WITHIN_TRAVEL_ENVELOPE"
        ),
        "previous_analysis_key": previous["key"],
        "elapsed_seconds": elapsed,
        "distance_km": round(distance, 2),
        "allowed_distance_km": round(allowed_distance, 2),
        "excess_distance_km": round(
            max(0, distance - allowed_distance), 2
        ),
        "effective_speed_kmh": (
            round(speed, 2) if speed is not None else None
        ),
        "evidence_kind": "declared_location_consistency",
    })

    if suspicious:
        result["signals"].append({
            "code": "IMPLAUSIBLE_TRAVEL",
            "evidence": dict(summary),
        })

    return result