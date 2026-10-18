from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from tests.integration.test_pix_analysis import (
    analysis_path,
    payload,
    send,
)


def document():
    return uuid4().hex[:12].upper() + "00"


@pytest.fixture
def isolated_payload(payload):
    item = deepcopy(payload)

    for account in ("source_account", "destination_account"):
        item[account]["owner"]["document_number"] = document()

    item["amount"] = 10_000
    return item


def create_analysis(item):
    response = send("POST", "/pix/transaction", json=item)

    assert response.status_code == 201, response.text

    return response.json()


def test_real_policy_approves_and_retries_do_not_inflate_history(
    isolated_payload
):
    item = isolated_payload
    first = create_analysis(item)

    assert first["status"] == "approved"
    assert first["metadata"]["policy_ready"] is True
    assert first["metadata"]["risk_score"] == 0

    for _ in range(3):
        repeated = send("POST", "/pix/transaction", json=item)

        assert repeated.status_code == 200, repeated.text
        assert repeated.json() == first

    another = deepcopy(item)
    another["id"] = f"pix-{uuid4().hex}"

    second = create_analysis(another)
    activity = second["metadata"]["context"]["activity"]

    assert activity["source_received"]["count_1m"] == 1
    assert activity["device_received"]["count_1m"] == 1

    fetched = send("GET", analysis_path(item["id"]))

    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == first


def test_concurrent_distinct_requests_cannot_all_see_empty_history(
    isolated_payload
):
    items = []

    for _ in range(8):
        item = deepcopy(isolated_payload)
        item["id"] = f"pix-{uuid4().hex}"
        items.append(item)

    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(executor.map(
            lambda item: send(
                "POST",
                "/pix/transaction",
                json=item,
            ),
            items,
        ))

    assert all(r.status_code == 201 for r in responses), [
        (r.status_code, r.text)
        for r in responses
    ]

    bodies = [r.json() for r in responses]

    observed = [
        body["metadata"]["context"]["activity"]
        ["source_received"]["count_1m"]
        for body in bodies
    ]

    assert sorted(observed) == list(range(8))
    assert len({body["key"] for body in bodies}) == 8

    for body, prior_count in zip(bodies, observed):
        expected = (
            "two_factor_required"
            if prior_count == 7
            else "approved"
        )

        assert body["status"] == expected, body


def test_backdating_event_time_does_not_bypass_arrival_velocity(
    isolated_payload
):
    event_at = datetime.fromisoformat(
        isolated_payload["transaction_date"]
    )
    last = None

    for index in range(8):
        item = deepcopy(isolated_payload)
        item["id"] = f"pix-{uuid4().hex}"
        item["transaction_date"] = (
            event_at - timedelta(days=index)
        ).isoformat()

        last = create_analysis(item)

        expected = (
            "two_factor_required"
            if index == 7
            else "approved"
        )

        assert last["status"] == expected, last

    activity = last["metadata"]["context"]["activity"]

    assert activity["source_event"]["count_1m"] == 0
    assert activity["source_received"]["count_1m"] == 7
    assert (
        "SOURCE_BURST_1M"
        in last["metadata"]["reason_codes"]
    )


def test_combined_risk_is_rejected_and_saved_as_a_business_decision(
    isolated_payload
):
    isolated_payload["destination_account"]["opening_date"] = (
        isolated_payload["transaction_date"]
    )

    for _ in range(7):
        item = deepcopy(isolated_payload)
        item["id"] = f"pix-{uuid4().hex}"

        assert create_analysis(item)["status"] == "approved"

    item = deepcopy(isolated_payload)
    item["id"] = f"pix-{uuid4().hex}"
    item["amount"] = 500_000

    result = create_analysis(item)

    assert result["status"] == "rejected"
    assert result["metadata"]["risk_score"] == 80
    assert set(result["metadata"]["reason_codes"]) == {
        "SOURCE_BURST_1M",
        "HIGH_AMOUNT",
        "RECENT_DESTINATION_ACCOUNT",
    }

    fetched = send("GET", analysis_path(item["id"]))

    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == result


def test_prior_approved_incoming_analyses_reveal_possible_forwarding(
    isolated_payload
):
    collector = (
        isolated_payload["source_account"]
        ["owner"]["document_number"]
    )

    for _ in range(3):
        incoming = deepcopy(isolated_payload)
        incoming["id"] = f"pix-{uuid4().hex}"
        incoming["amount"] = 40_000

        incoming["source_account"]["owner"]["document_number"] = (
            document()
        )
        incoming["source"]["device_id"] = (
            f"device-{uuid4().hex}"
        )
        incoming["destination_account"]["owner"][
            "document_number"
        ] = collector

        assert create_analysis(incoming)["status"] == "approved"

    outgoing = deepcopy(isolated_payload)
    outgoing["id"] = f"pix-{uuid4().hex}"
    outgoing["amount"] = 84_000

    result = create_analysis(outgoing)

    assert result["status"] == "two_factor_required"
    assert result["metadata"]["risk_score"] == 45
    assert result["metadata"]["reason_codes"] == [
        "RAPID_FORWARDING_PATTERN",
    ]

SP = {"latitude": -23.5505, "longitude": -46.6333}
RIO = {"latitude": -22.9068, "longitude": -43.1729}


def previous_request(item):
    previous = deepcopy(item)
    previous["id"] = f"geo-{uuid4().hex}"

    event_at = datetime.fromisoformat(
        item["transaction_date"]
    )
    previous["transaction_date"] = (
        event_at - timedelta(minutes=10)
    ).isoformat()

    previous["source"]["geolocation"] = dict(SP)

    return previous


def test_geographic_change_requires_step_up_and_is_idempotent(
    isolated_payload
):
    previous = previous_request(isolated_payload)

    assert create_analysis(previous)["status"] == "approved"

    isolated_payload["source"]["geolocation"] = dict(RIO)
    result = create_analysis(isolated_payload)

    assert result["status"] == "two_factor_required"
    assert result["metadata"]["risk_score"] == 35
    assert result["metadata"]["reason_codes"] == [
        "IMPLAUSIBLE_TRAVEL",
    ]
    assert (
        result["metadata"]["context"]["geography"]
        ["previous_analysis_key"]
    )

    repeated = send(
        "POST",
        "/pix/transaction",
        json=isolated_payload,
    )

    assert repeated.status_code == 200, repeated.text
    assert repeated.json() == result

    changed = deepcopy(isolated_payload)
    changed["source"]["geolocation"] = dict(SP)

    conflict = send(
        "POST",
        "/pix/transaction",
        json=changed,
    )

    assert conflict.status_code == 409, conflict.text

    fetched = send(
        "GET",
        analysis_path(isolated_payload["id"]),
    )

    assert fetched.json() == result


def test_different_devices_do_not_create_a_false_travel_alarm(
    isolated_payload
):
    assert (
        create_analysis(previous_request(isolated_payload))[
            "status"
        ] == "approved"
    )

    isolated_payload["source"].update(
        device_id=f"another-{uuid4().hex}",
        geolocation=dict(RIO),
    )

    result = create_analysis(isolated_payload)

    assert result["status"] == "approved"
    assert result["metadata"]["risk_score"] == 0
    assert (
        result["metadata"]["context"]["geography"][
            "assessed"
        ] is False
    )

def test_api_exports_frequency_limits_and_policy_version(
    isolated_payload
):
    result = create_analysis(isolated_payload)

    assert result["metadata"]["policy_version"] == "sentinel24-1.10"

    frequency = result["metadata"]["context"]["frequency_profile"]

    for clock in ("received", "event"):
        assert frequency[clock]["mature"] is False
        assert frequency[clock]["effective_limits"] == {
            "1m": 8,
            "5m": 20,
        }
    device = result["metadata"]["context"]["device_profile"]

    assert device["assessed"] is False
    assert device["prior_distinct_payers_1h"] == 0
    assert device["unfamiliar_payers_1h"] == 0

def test_api_exports_recipient_reference(isolated_payload):
    result = create_analysis(isolated_payload)
    metadata = result["metadata"]

    assert metadata["policy_version"] == "sentinel24-1.10"
    assert metadata["parameters"]["recipient_peak_multiplier"] == 2
    recipient = metadata["context"]["recipient_profile"]

    for clock in ("received", "event"):
        assert recipient[clock]["assessed"] is False
        assert recipient[clock]["mature"] is False
        assert recipient[clock]["effective_limit_5m"] == 8