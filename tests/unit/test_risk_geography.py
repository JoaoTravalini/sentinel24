from copy import deepcopy
from datetime import datetime, timedelta
import json

import pytest

from rules.engine import RiskEngine
from rules.geography import distance_km
from tests.unit.test_risk_engine import scenario


SP = {"latitude": -23.5505, "longitude": -46.6333}
RIO = {"latitude": -22.9068, "longitude": -43.1729}


@pytest.fixture
def located(scenario):
    payload, context = deepcopy(scenario)

    payload["source"] = {
        "device_id": "same-device",
        "geolocation": dict(RIO),
    }

    event_at = datetime.fromisoformat(
        payload["transaction_date"]
    )

    context["previous_location"] = {
        "key": "previous-analysis",
        "device_id": "same-device",
        "transaction_at": (
            event_at - timedelta(minutes=10)
        ).isoformat(),
        "geolocation": dict(SP),
    }

    return payload, context


def decide(located):
    return RiskEngine().evaluate(*located)


@pytest.mark.parametrize("first,second,expected", [
    ((0, 0), (0, 0), 0),
    ((0, 0), (0, 90), 10_007.56),
    ((0, 0), (0, 180), 20_015.11),
    ((0, 179), (0, -179), 222.39),
])
def test_distance_geometry(first, second, expected):
    actual = distance_km(
        {
            "latitude": first[0],
            "longitude": first[1],
        },
        {
            "latitude": second[0],
            "longitude": second[1],
        },
    )

    assert actual == pytest.approx(expected, abs=0.1)


@pytest.mark.parametrize("missing", ["current", "previous"])
def test_missing_location_does_not_add_risk(
    located, missing
):
    if missing == "current":
        located[0]["source"].pop("geolocation")
    else:
        located[1]["previous_location"] = None

    result = decide(located)

    assert result["status"] == "approved"
    assert (
        result["metadata"]["context"]
        ["geography"]["assessed"] is False
    )


@pytest.mark.parametrize("seconds,status,assessed", [
    (600, "two_factor_required", True),
    (3600, "approved", True),
    (0, "two_factor_required", True),
    (86400, "approved", True),
    (86401, "approved", False),
    (-1, "approved", False),
])
def test_travel_time_and_window_boundaries(
    located, seconds, status, assessed
):
    event_at = datetime.fromisoformat(
        located[0]["transaction_date"]
    )

    located[1]["previous_location"]["transaction_at"] = (
        event_at - timedelta(seconds=seconds)
    ).isoformat()

    result = decide(located)

    assert result["status"] == status
    assert (
        result["metadata"]["context"]
        ["geography"]["assessed"] is assessed
    )


def test_zero_coordinates_are_valid(located):
    located[0]["source"]["geolocation"] = {
        "latitude": 0,
        "longitude": 0,
    }
    located[1]["previous_location"]["geolocation"] = {
        "latitude": 0,
        "longitude": 0,
    }

    result = decide(located)

    assert result["status"] == "approved"
    assert (
        result["metadata"]["context"]
        ["geography"]["assessed"] is True
    )


def test_small_location_jump_is_not_a_travel_alarm(located):
    located[0]["source"]["geolocation"] = {
        "latitude": 0,
        "longitude": 0.01,
    }
    located[1]["previous_location"]["geolocation"] = {
        "latitude": 0,
        "longitude": 0,
    }

    assert decide(located)["status"] == "approved"


def test_different_devices_are_not_one_physical_trip(located):
    located[0]["source"]["device_id"] = "another-device"

    assert decide(located)["status"] == "approved"


def test_geography_and_shared_device_do_not_multiply_access_risk(
    located
):
    located[1]["activity"]["device_received"][
        "counterparties_1h"
    ] = 3

    result = decide(located)

    assert result["status"] == "two_factor_required"
    assert result["metadata"]["risk_score"] == 35


def test_geography_combines_with_value_and_recent_destination(
    located
):
    located[0]["amount"] = 500_000
    located[0]["destination_account"]["opening_date"] = (
        located[0]["transaction_date"]
    )

    result = decide(located)

    assert result["status"] == "two_factor_required"
    assert result["metadata"]["risk_score"] == 80
    assert result["metadata"]["strong_dynamic_signal"] is False
    assert result["metadata"]["rejection_support_codes"] == []


def test_geographic_explanation_does_not_export_coordinates(
    located
):
    result = decide(located)

    assert (
        "IMPLAUSIBLE_TRAVEL"
        in result["metadata"]["reason_codes"]
    )

    serialized = json.dumps(result)

    assert "latitude" not in serialized
    assert "longitude" not in serialized