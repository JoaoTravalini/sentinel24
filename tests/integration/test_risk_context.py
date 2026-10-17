from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from database import Context
from models import PixAnalysis
from repositories.risk_context_repository import RiskContextRepository


NOW = datetime(2026, 10, 17, 18, tzinfo=timezone.utc)


def document():
    return uuid4().hex[:12].upper() + "00"


@pytest.fixture
def history():
    context = Context()
    session = context.get_or_create_session()

    source, destination = document(), document()
    device = f"device-{uuid4().hex}"

    payload = {
        "id": f"pix-{uuid4().hex}",
        "amount": 10000,
        "transaction_date": NOW.isoformat(),
        "source_account": {
            "branch": "0001",
            "account_number": "88213",
            "opening_date": (NOW - timedelta(days=365)).isoformat(),
            "owner": {"document_number": source},
        },
        "destination_account": {
            "participant": "60701190",
            "branch": "3675",
            "account_number": "10442",
            "opening_date": (NOW - timedelta(days=100)).isoformat(),
            "owner": {"document_number": destination},
        },
        "source": {
            "device_id": device,
            "channel": "app",
            "ip": "198.51.100.23",
        },
    }

    def add(
        *,
        amount=10000,
        status="approved",
        event_at=None,
        received_at=None,
        source_document=None,
        destination_document=None,
        device_id=None,
    ):
        row = PixAnalysis(
            analysis_key=uuid4(),
            pix_id=f"history-{uuid4().hex}",
            payload_hash="0" * 64,
            amount=amount,
            transaction_at=event_at or NOW - timedelta(seconds=10),
            received_at=received_at or NOW - timedelta(seconds=10),
            source_document=source_document or source,
            destination_document=destination_document or destination,
            device_id=device_id or device,
            source_ip="198.51.100.23",
            channel="app",
            status=status,
            policy_version="fixture-1",
            raw_payload={"source": {"geolocation": None}},
            decision_metadata={"synthetic": True},
        )

        session.add(row)
        return row

    def load():
        session.flush()
        return RiskContextRepository(context).load(payload, NOW)

    try:
        yield payload, add, load
    finally:
        session.rollback()
        session.close()


def test_empty_history_has_zero_counters(history):
    _, _, load = history
    result = load()

    assert len(result["activity"]) == 7
    assert all(
        value == 0
        for metrics in result["activity"].values()
        for value in metrics.values()
    )
    assert result["source_profile"]["sample_count"] == 0


def test_window_boundaries_and_exact_amounts(history):
    _, add, load = history

    ages = [59, 60, 61, 300, 301, 3600, 3601, 86400, 86401]

    for index, seconds in enumerate(ages, start=1):
        timestamp = NOW - timedelta(seconds=seconds)

        add(
            amount=index * 100,
            event_at=timestamp,
            received_at=timestamp,
        )

    metrics = load()["activity"]["source_received"]

    assert metrics["count_1m"] == 2
    assert metrics["amount_cents_1m"] == 300
    assert metrics["count_5m"] == 4
    assert metrics["amount_cents_5m"] == 1000
    assert metrics["count_1h"] == 6
    assert metrics["amount_cents_1h"] == 2100
    assert metrics["count_24h"] == 8
    assert metrics["amount_cents_24h"] == 3600


def test_backdating_and_future_information_are_separated(history):
    _, add, load = history

    add(event_at=NOW - timedelta(days=31))
    add(event_at=NOW + timedelta(minutes=1))
    add(received_at=NOW + timedelta(seconds=1))

    activity = load()["activity"]

    assert activity["source_received"]["count_1m"] == 2
    assert activity["source_event"]["count_1m"] == 0


def test_velocity_counts_all_decisions(history):
    _, add, load = history

    add(amount=100, status="approved")
    add(amount=200, status="two_factor_required")
    add(amount=300, status="rejected")

    metrics = load()["activity"]["source_received"]

    assert metrics["count_5m"] == 3
    assert metrics["amount_cents_5m"] == 600
    assert metrics["approved_count_5m"] == 1
    assert metrics["approved_amount_cents_5m"] == 100
    assert metrics["rejected_count_5m"] == 1


def test_incoming_activity_distinguishes_approved_attempts(history):
    payload, add, load = history
    source = payload["source_account"]["owner"]["document_number"]

    add(
        source_document=document(),
        destination_document=source,
        amount=100,
    )
    add(
        source_document=document(),
        destination_document=source,
        amount=200,
    )
    add(
        source_document=document(),
        destination_document=source,
        amount=900,
        status="two_factor_required",
    )

    metrics = load()["activity"]["source_incoming_received"]

    assert metrics["count_5m"] == 3
    assert metrics["approved_count_5m"] == 2
    assert metrics["approved_counterparties_5m"] == 2
    assert metrics["approved_amount_cents_5m"] == 300


def test_device_activity_groups_distinct_payers(history):
    _, add, load = history

    for _ in range(3):
        add(source_document=document())

    activity = load()["activity"]

    assert activity["device_received"]["counterparties_1h"] == 3
    assert activity["source_received"]["count_1h"] == 0


def test_profile_excludes_recent_rejected_and_unobserved_requests(history):
    _, add, load = history

    add(amount=10000, event_at=NOW - timedelta(days=2))
    add(amount=20000, event_at=NOW - timedelta(hours=1))
    add(amount=999999)
    add(status="rejected", event_at=NOW - timedelta(days=2))
    add(
        status="two_factor_required",
        event_at=NOW - timedelta(days=2),
    )
    add(event_at=NOW - timedelta(days=31))
    add(
        event_at=NOW - timedelta(hours=1),
        received_at=NOW + timedelta(seconds=1),
    )

    profile = load()["source_profile"]

    assert profile["sample_count"] == 2
    assert [
        entry["amount_cents"] for entry in profile["entries"]
    ] == [20000, 10000]
    assert all(
        entry["geolocation"] is None
        for entry in profile["entries"]
    )


def test_profile_is_bounded_and_reports_truncation(history):
    _, add, load = history

    for index in range(101):
        add(
            amount=index + 1,
            event_at=NOW - timedelta(days=1, seconds=index),
        )

    profile = load()["source_profile"]

    assert profile["sample_count"] == 100
    assert profile["truncated"] is True
    assert profile["entries"][0]["amount_cents"] == 1
    assert profile["entries"][-1]["amount_cents"] == 100


def test_amount_aggregation_does_not_use_float(history):
    _, add, load = history

    add(amount=9_000_000_000_000_000_000)
    add(amount=9_000_000_000_000_000_000)

    metrics = load()["activity"]["source_received"]

    assert (
        metrics["amount_cents_1m"]
        == 18_000_000_000_000_000_000
    )
    assert isinstance(metrics["amount_cents_1m"], int)


def test_unrelated_records_and_current_id_do_not_enter_context(history):
    payload, add, load = history

    add(
        source_document=document(),
        destination_document=document(),
        device_id=f"other-{uuid4().hex}",
    )

    current = add()
    current.pix_id = payload["id"]

    activity = load()["activity"]

    assert all(
        metrics["count_24h"] == 0
        for metrics in activity.values()
    )