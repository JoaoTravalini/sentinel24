from copy import deepcopy
from datetime import datetime
from math import degrees
from types import SimpleNamespace
from uuid import uuid4

import pytest

from database import SessionLocal
from repositories.pix_analysis_repository import PixAnalysisRepository
from tests.integration.test_pix_analysis import (
    analysis_path,
    payload,  # noqa: F401
    send,
)
from tests.integration.test_risk_decisions import (
    RIO,
    SP,
    create_analysis,
    isolated_payload,  # noqa: F401
    previous_request,
)
from utils.pix_payload import calculate_payload_hash, prepare_pix_payload


def test_geography_with_static_factors_requires_step_up(isolated_payload):
    item = isolated_payload
    assert create_analysis(previous_request(item))["status"] == "approved"

    item["amount"] = 500_000
    item["destination_account"]["opening_date"] = item["transaction_date"]
    item["source"]["geolocation"] = RIO

    result = create_analysis(item)
    metadata = result["metadata"]

    assert result["status"] == "two_factor_required", result
    assert metadata["policy_version"] == "sentinel24-1.8"
    assert metadata["risk_score"] == 80
    assert metadata["strong_dynamic_signal"] is False
    assert metadata["rejection_support_codes"] == []

    repeated = send("POST", "/pix/transaction", json=item)
    fetched = send("GET", analysis_path(item["id"]))

    assert repeated.status_code == 200, repeated.text
    assert fetched.status_code == 200, fetched.text
    assert repeated.json() == fetched.json() == result


def test_geography_with_arrival_burst_can_still_reject(isolated_payload):
    item = isolated_payload
    assert create_analysis(previous_request(item))["status"] == "approved"

    for _ in range(6):
        previous = deepcopy(item)
        previous["id"] = f"geo-{uuid4().hex}"
        previous["source"]["geolocation"] = SP
        assert create_analysis(previous)["status"] == "approved"

    item["source"]["geolocation"] = RIO
    result = create_analysis(item)
    metadata = result["metadata"]

    assert result["status"] == "rejected", result
    assert metadata["risk_score"] == 70
    assert metadata["strong_dynamic_signal"] is True
    assert metadata["rejection_support_codes"] == ["SOURCE_BURST_1M"]
    assert "IMPLAUSIBLE_TRAVEL" in metadata["reason_codes"]
    assert (
        metadata["context"]["activity"]["source_received"]["count_1m"]
        == 7
    )


def test_eighty_kilometers_at_same_time_is_assessed(isolated_payload):
    item = isolated_payload
    previous = deepcopy(item)
    previous["id"] = f"geo-{uuid4().hex}"
    previous["source"]["geolocation"] = {
        "latitude": 0,
        "longitude": 0,
    }
    assert create_analysis(previous)["status"] == "approved"

    item["source"]["geolocation"] = {
        "latitude": 0,
        "longitude": degrees(80 / 6371.0088),
    }
    result = create_analysis(item)
    metadata = result["metadata"]
    geography = metadata["context"]["geography"]

    assert result["status"] == "two_factor_required", result
    assert metadata["risk_score"] == 35
    assert metadata["rejection_support_codes"] == []
    assert geography["assessed"] is True
    assert geography["elapsed_seconds"] == 0
    assert geography["distance_km"] == pytest.approx(80, abs=0.01)
    assert geography["allowed_distance_km"] == pytest.approx(
        63.33, abs=0.01
    )
    assert geography["excess_distance_km"] == pytest.approx(
        16.67, abs=0.01
    )


def test_previous_policy_decision_is_replayed_unchanged(isolated_payload):
    item = isolated_payload
    normalized = prepare_pix_payload(item)
    decision = {
        "status": "rejected",
        "metadata": {
            "policy_version": "sentinel24-1.6",
            "policy_ready": True,
            "risk_score": 80,
            "synthetic": True,
        },
    }

    # Simula uma decisão histórica persistida sob a política anterior.
    with SessionLocal.begin() as session:
        repository = PixAnalysisRepository(
            SimpleNamespace(db_session=session)
        )
        analysis = repository.create(
            raw_payload=deepcopy(item),
            normalized=normalized,
            payload_hash=calculate_payload_hash(normalized),
            decision=decision,
            received_at=datetime.fromisoformat(
                normalized["transaction_date"]
            ),
        )
        expected = {
            "key": str(analysis.analysis_key),
            "status": analysis.status,
            "metadata": deepcopy(analysis.decision_metadata),
        }

    repeated = send("POST", "/pix/transaction", json=item)
    fetched = send("GET", analysis_path(item["id"]))

    assert repeated.status_code == 200, repeated.text
    assert fetched.status_code == 200, fetched.text
    assert repeated.json() == fetched.json() == expected

    changed = deepcopy(item)
    changed["amount"] += 1

    conflict = send("POST", "/pix/transaction", json=changed)
    assert conflict.status_code == 409, conflict.text