from datetime import timedelta

import pytest

from rules.engine import RiskEngine
from tests.integration.test_risk_context import (
    NOW, document, history,  # noqa: F401
)


def reference(add, amount=10_000):
    for day in (2, 3, 4):
        at = NOW - timedelta(days=day)
        for _ in range(4):
            add(amount=amount, event_at=at, received_at=at)


def recent(add, amount=90_000, hours=False):
    for n in (2, 4):
        delta = (
            timedelta(hours=n)
            if hours
            else timedelta(minutes=n * 10)
        )
        at = NOW - delta
        add(amount=amount, event_at=at, received_at=at)


def test_database_reference_preserves_same_timestamp_payments(history):
    payload, add, load = history
    reference(add)
    recent(add)
    payload["amount"] = 90_000
    context = load()
    profile = context["amount_profile"]

    assert profile["sample_count"] == 12
    assert profile["observed_span_seconds"] == 172_800

    assert [
        d["peak_adjusted_cents_1h"]
        for d in profile["daily_peaks"]
    ] == [30_000] * 3

    assert [
        d["peak_adjusted_cents_24h"]
        for d in profile["daily_peaks"]
    ] == [30_000, 70_000, 70_000]

    result = RiskEngine().evaluate(payload, context)

    assert result["status"] == "two_factor_required"
    assert result["metadata"]["family_scores"] == {"velocity": 35}
    assert set(result["metadata"]["reason_codes"]) == {
        "ACCUMULATED_AMOUNT_1H",
        "ACCUMULATED_AMOUNT_24H",
    }


def test_habitual_high_volume_is_not_penalized(history):
    payload, add, load = history
    reference(add, amount=200_000)
    recent(add, amount=200_000)
    payload["amount"] = 200_000

    result = RiskEngine().evaluate(payload, load())
    summary = result["metadata"]["context"]["amount_profile"]

    assert summary["mature"] is True
    assert (
        summary["windows"]["1h"]["effective_limit_cents"]
        == 1_200_000
    )
    assert result["status"] == "approved"
    assert result["metadata"]["risk_score"] == 0


def test_rent_and_small_purchases_skip_reference_query(history):
    payload, add, load = history

    for minutes, amount in ((20, 2_000), (40, 3_000)):
        at = NOW - timedelta(minutes=minutes)
        add(amount=amount, event_at=at, received_at=at)

    payload["amount"] = 150_000
    context = load()
    result = RiskEngine().evaluate(payload, context)

    assert context["amount_profile"]["assessed"] is False
    assert result["status"] == "approved"
    assert result["metadata"]["risk_score"] == 0

    summary = result["metadata"]["context"]["amount_profile"]
    assert summary["windows"]["24h"]["adjusted_cents"] == 5_000


def test_spaced_payments_are_detected_by_daily_window(history):
    payload, add, load = history
    reference(add)
    recent(add, hours=True)
    payload["amount"] = 90_000

    result = RiskEngine().evaluate(payload, load())

    assert result["status"] == "two_factor_required"
    assert result["metadata"]["reason_codes"] == [
        "ACCUMULATED_AMOUNT_24H",
    ]
    assert result["metadata"]["risk_score"] == 35


@pytest.mark.parametrize("kind", [
    "rejected",
    "two_factor_required",
    "self",
    "other_owner",
    "recent_backdated",
    "future_received",
    "expired",
    "current_id",
])
def test_ineligible_records_do_not_train_amount_reference(history, kind):
    payload, add, load = history
    reference(add)
    recent(add)
    payload["amount"] = 90_000
    at = NOW - timedelta(days=3)

    kwargs = {
        "amount": 9_000_000,
        "event_at": at,
        "received_at": at,
    }

    if kind in ("rejected", "two_factor_required"):
        kwargs["status"] = kind
    elif kind == "self":
        kwargs["destination_document"] = (
            payload["source_account"]["owner"]["document_number"]
        )
    elif kind == "other_owner":
        kwargs["source_document"] = document()
    elif kind == "recent_backdated":
        kwargs["received_at"] = NOW - timedelta(hours=2)
    elif kind == "future_received":
        kwargs["received_at"] = NOW + timedelta(seconds=1)
    elif kind == "expired":
        kwargs["event_at"] = NOW - timedelta(days=16)
        kwargs["received_at"] = NOW - timedelta(days=16)

    row = add(**kwargs)

    if kind == "current_id":
        row.pix_id = payload["id"]

    profile = load()["amount_profile"]

    assert profile["sample_count"] == 12
    assert [
        d["peak_adjusted_cents_1h"]
        for d in profile["daily_peaks"]
    ] == [30_000] * 3


@pytest.mark.parametrize("window,seconds", [
    ("1h", 3600),
    ("24h", 86400),
])
def test_current_windows_include_exact_boundary(history, window, seconds):
    _, add, load = history

    at = NOW - timedelta(seconds=seconds)
    add(amount=10_000, event_at=at, received_at=at)

    older = at - timedelta(microseconds=1)
    add(amount=20_000, event_at=older, received_at=older)

    metrics = load()["activity"]["source_received"]

    assert metrics[f"approved_third_party_count_{window}"] == 1
    assert (
        metrics[f"approved_third_party_amount_cents_{window}"]
        == 10_000
    )
    assert metrics[f"approved_third_party_max_cents_{window}"] == 10_000


def test_padding_contributes_to_windows_but_not_maturity(history):
    payload, add, load = history
    recent(add)
    payload["amount"] = 90_000

    start = NOW - timedelta(days=14)
    before = start - timedelta(minutes=30)

    add(amount=20_000, event_at=before, received_at=before)
    add(amount=10_000, event_at=start, received_at=start)

    profile = load()["amount_profile"]

    assert profile["sample_count"] == 1
    assert profile["observed_span_seconds"] == 0
    assert len(profile["daily_peaks"]) == 1

    day = profile["daily_peaks"][0]
    assert day["peak_adjusted_cents_1h"] == 10_000
    assert day["peak_adjusted_cents_24h"] == 10_000


def test_backdated_event_dates_do_not_create_observed_maturity(history):
    payload, add, load = history
    recent(add)
    payload["amount"] = 90_000

    for day in (2, 3, 4):
        for _ in range(4):
            add(
                event_at=NOW - timedelta(days=day),
                received_at=NOW - timedelta(hours=2),
            )

    profile = load()["amount_profile"]

    assert profile["assessed"] is True
    assert profile["sample_count"] == 0
    assert profile["daily_peaks"] == []


def test_owner_volume_survives_device_and_account_changes(history):
    payload, add, load = history
    reference(add)

    for index in (1, 2):
        at = NOW - timedelta(minutes=index * 20)

        row = add(
            amount=90_000,
            event_at=at,
            received_at=at,
            device_id=f"other-device-{index}",
        )

        row.raw_payload = {
            "source": {"geolocation": None},
            "source_account": {
                "branch": "0002",
                "account_number": str(index),
            },
        }

    payload["amount"] = 90_000
    payload["source_account"]["branch"] = "0003"
    payload["source"]["device_id"] = "current-device"
    context = load()

    metrics = context["activity"]["source_received"]
    assert metrics["approved_third_party_count_1h"] == 2
    assert context["amount_profile"]["sample_count"] == 12

    result = RiskEngine().evaluate(payload, context)
    assert (
        "ACCUMULATED_AMOUNT_1H"
        in result["metadata"]["reason_codes"]
    )


def test_cold_start_does_not_invent_personalized_amount_risk(history):
    payload, add, load = history
    recent(add)
    payload["amount"] = 90_000

    result = RiskEngine().evaluate(payload, load())
    summary = result["metadata"]["context"]["amount_profile"]

    assert summary["assessed"] is True
    assert summary["mature"] is False
    assert result["status"] == "approved"
    assert result["metadata"]["risk_score"] == 0


def test_amount_and_independent_access_change_can_reject(history):
    payload, add, load = history
    reference(add)
    recent(add)
    payload["amount"] = 90_000

    payload["source"].update(
        device_id="new-device",
        ip="203.0.113.99",
    )

    result = RiskEngine().evaluate(payload, load())
    metadata = result["metadata"]

    assert result["status"] == "rejected"
    assert metadata["risk_score"] == 70
    assert metadata["family_scores"] == {
        "access_context": 35,
        "velocity": 35,
    }
    assert metadata["rejection_support_codes"] == [
        "ACCESS_CONTEXT_CHANGE",
    ]