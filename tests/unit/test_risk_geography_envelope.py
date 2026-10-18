from datetime import datetime, timedelta, timezone
import json
from math import pi

import pytest

import rules.geography as geography


def trip(distance, seconds):
    now = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)

    payload = {
        "transaction_date": now.isoformat(),
        "source": {
            "device_id": "same-device",
            "geolocation": {
                "latitude": 0,
                "longitude": distance / 6371.0088 * 180 / pi,
            },
        },
    }

    context = {
        "previous_location": {
            "key": "previous-analysis",
            "device_id": "same-device",
            "transaction_at": (
                now - timedelta(seconds=seconds)
            ).isoformat(),
            "geolocation": {
                "latitude": 0,
                "longitude": 0,
            },
        },
    }

    return payload, context


@pytest.mark.parametrize("distance,seconds,signal", [
    (0, 0, False),
    (1, 0, False),
    (40, 0, False),
    (80, 0, True),
    (80, 600, False),
    (400, 600, True),
    (400, 3600, False),
    (900, 3600, False),
    (1500, 7200, False),
    (2000, 600, True),
    (6000, 43200, False),
    (10000, 600, True),
])
def test_distinct_travel_distances_and_intervals(
    distance, seconds, signal
):
    result = geography.evaluate_geography(
        *trip(distance, seconds)
    )

    assert bool(result["signals"]) is signal
    assert result["summary"]["assessed"] is True


@pytest.mark.parametrize("distance,signal", [
    (99.99, False),
    (100, False),
    (100.01, True),
])
def test_exact_envelope_boundary(
    monkeypatch, distance, signal
):
    monkeypatch.setattr(
        geography, "distance_km", lambda *_: distance
    )

    result = geography.evaluate_geography(
        *trip(distance, 132)
    )

    assert result["summary"]["allowed_distance_km"] == 100
    assert bool(result["signals"]) is signal


def test_clear_cases_remain_stable_across_policy_tolerances(
    monkeypatch,
):
    for speed, distance, seconds in (
        (800, 10, 60),
        (1000, 30, 120),
        (1200, 50, 180),
    ):
        monkeypatch.setitem(
            geography.GEOGRAPHY_PARAMETERS,
            "geo_maximum_speed_kmh",
            speed,
        )
        monkeypatch.setitem(
            geography.GEOGRAPHY_PARAMETERS,
            "geo_distance_tolerance_km",
            distance,
        )
        monkeypatch.setitem(
            geography.GEOGRAPHY_PARAMETERS,
            "geo_time_tolerance_seconds",
            seconds,
        )

        assert not geography.evaluate_geography(
            *trip(400, 3600)
        )["signals"]

        assert geography.evaluate_geography(
            *trip(2000, 600)
        )["signals"]


def test_near_boundary_outcome_depends_on_policy_tolerances(
    monkeypatch,
):
    assert geography.evaluate_geography(
        *trip(80, 0)
    )["signals"]

    monkeypatch.setitem(
        geography.GEOGRAPHY_PARAMETERS,
        "geo_maximum_speed_kmh",
        1200,
    )
    monkeypatch.setitem(
        geography.GEOGRAPHY_PARAMETERS,
        "geo_distance_tolerance_km",
        50,
    )
    monkeypatch.setitem(
        geography.GEOGRAPHY_PARAMETERS,
        "geo_time_tolerance_seconds",
        180,
    )

    assert not geography.evaluate_geography(
        *trip(80, 0)
    )["signals"]


def test_zero_elapsed_and_zero_padding_keep_valid_json(
    monkeypatch,
):
    monkeypatch.setitem(
        geography.GEOGRAPHY_PARAMETERS,
        "geo_time_tolerance_seconds",
        0,
    )

    result = geography.evaluate_geography(*trip(80, 0))

    assert result["signals"]
    assert result["summary"]["effective_speed_kmh"] is None

    json.dumps(result, allow_nan=False)