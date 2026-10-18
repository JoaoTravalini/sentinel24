from datetime import timedelta
from uuid import uuid4

import pytest

from rules.recipient import evaluate_recipient
from tests.integration.test_risk_context import NOW, document, history
from rules.engine import RiskEngine


def recent_receipts(add, count=7):
    for _ in range(count):
        add(
            source_document=document(),
            device_id=f"receipt-{uuid4().hex}",
        )


def historical_peaks(add, count=12, status="approved"):
    for day in (2, 3, 4):
        for index in range(count):
            stamp = NOW - timedelta(days=day, seconds=index * 5)
            add(
                source_document=document(),
                device_id=f"history-{uuid4().hex}",
                event_at=stamp,
                received_at=stamp,
                status=status,
            )


def test_receiver_reference_aggregates_different_payers_and_devices(history):
    _, add, load = history
    historical_peaks(add)
    recent_receipts(add)
    context = load()

    assert context["recipient_profile"]["assessed"] is True
    for profile in context["recipient_profile"]["clocks"].values():
        assert [
            day["peak_5m"] for day in profile["daily_peaks"]
        ] == [12] * 3
        assert profile["observed_days"] == 3
        assert profile["observed_span_seconds"] >= 48 * 3600

    result = evaluate_recipient(context)
    assert result["signals"] == []
    assert result["summary"]["received"]["effective_limit_5m"] == 24


@pytest.mark.parametrize("status", ["rejected", "two_factor_required"])
def test_unapproved_receipts_do_not_raise_the_limit(history, status):
    _, add, load = history
    historical_peaks(add, status=status)
    recent_receipts(add)
    context = load()

    profile = context["recipient_profile"]["clocks"]["received"]
    assert profile["daily_peaks"] == []

    evidence = evaluate_recipient(context)["signals"][0]["evidence"]
    assert evidence["effective_limit"] == 8


def test_reference_excludes_other_receivers_recent_expired_and_current(history):
    payload, add, load = history
    old = NOW - timedelta(days=2)
    add(event_at=old, received_at=old)
    add(event_at=old, received_at=old, destination_document=document())
    add(event_at=old, received_at=NOW - timedelta(hours=1))
    expired = NOW - timedelta(days=15)
    add(event_at=expired, received_at=expired)
    current = add(event_at=old, received_at=old)
    current.pix_id = payload["id"]
    recent_receipts(add)

    for profile in load()["recipient_profile"]["clocks"].values():
        assert len(profile["daily_peaks"]) == 1
        assert profile["daily_peaks"][0]["peak_5m"] == 1


def test_backdated_arrivals_cannot_instantly_train_receiver_reference(history):
    _, add, load = history
    for day in (2, 3, 4):
        add(
            event_at=NOW - timedelta(days=day),
            received_at=NOW - timedelta(hours=1),
        )
    recent_receipts(add)

    for profile in load()["recipient_profile"]["clocks"].values():
        assert profile["daily_peaks"] == []


def test_event_dates_do_not_replace_observed_maturity(history):
    _, add, load = history
    for day in (2, 3, 4):
        add(
            event_at=NOW - timedelta(days=day),
            received_at=NOW - timedelta(days=2),
        )
    recent_receipts(add)
    profile = evaluate_recipient(load())["summary"]["event"]

    assert profile["active_days"] == 3
    assert profile["observed_days"] == 1
    assert profile["mature"] is False
    assert profile["effective_limit_5m"] == 8


def test_receiver_rolling_window_includes_exact_five_minute_boundary(history):
    _, add, load = history
    old = NOW - timedelta(days=2)
    for seconds in (0, 300, 601):
        stamp = old + timedelta(seconds=seconds)
        add(event_at=stamp, received_at=stamp)
    recent_receipts(add)

    for profile in load()["recipient_profile"]["clocks"].values():
        assert profile["daily_peaks"][0]["peak_5m"] == 2


def test_growth_still_signals_for_an_established_receiver(history):
    _, add, load = history
    historical_peaks(add)
    recent_receipts(add, count=23)
    result = evaluate_recipient(load())

    assert {s["code"] for s in result["signals"]} == {
        "RECIPIENT_FAN_IN",
    }
    assert all(
        s["evidence"]["effective_limit"] == 24
        for s in result["signals"]
    )


def test_receiver_history_is_skipped_below_initial_volume(history):
    _, add, load = history
    historical_peaks(add)
    recent_receipts(add, count=6)

    assert load()["recipient_profile"]["assessed"] is False

def prepare_payer_profile(add):
    known_destination = document()
    for index in range(12):
        stamp = NOW - timedelta(days=2, hours=index * 4)
        add(
            destination_document=known_destination,
            event_at=stamp,
            received_at=stamp,
        )


def test_first_payment_to_habitually_busy_receiver_is_approved(history):
    payload, add, load = history
    prepare_payer_profile(add)
    historical_peaks(add)
    recent_receipts(add, count=11)

    result = RiskEngine().evaluate(payload, load())

    assert result["status"] == "approved"
    assert result["metadata"]["risk_score"] == 15
    assert result["metadata"]["reason_codes"] == [
        "DESTINATION_NOT_IN_RECENT_PROFILE",
    ]
    recipient = result["metadata"]["context"]["recipient_profile"]
    for profile in recipient.values():
        assert profile["assessed"] is True
        assert profile["mature"] is True
        assert profile["effective_limit_5m"] == 24


def test_unusual_receiving_growth_and_new_payee_require_step_up(history):
    payload, add, load = history
    prepare_payer_profile(add)
    historical_peaks(add)
    recent_receipts(add, count=23)

    result = RiskEngine().evaluate(payload, load())

    assert result["status"] == "two_factor_required"
    assert result["metadata"]["risk_score"] == 40
    assert set(result["metadata"]["reason_codes"]) == {
        "RECIPIENT_FAN_IN",
        "DESTINATION_NOT_IN_RECENT_PROFILE",
    }
    signal = next(
        s for s in result["metadata"]["signals"]
        if s["code"] == "RECIPIENT_FAN_IN"
    )
    assert len(signal["evidence"]) == 2


@pytest.mark.parametrize("amount,status,score", [
    (10_000, "two_factor_required", 50),
    (150_000, "rejected", 75),
])
def test_habitual_receiving_does_not_override_other_risk(
    history, amount, status, score
):
    payload, add, load = history
    prepare_payer_profile(add)
    historical_peaks(add)
    recent_receipts(add, count=11)
    payload["amount"] = amount
    payload["source"].update(
        device_id=f"new-{uuid4().hex}",
        ip="203.0.113.99",
    )

    result = RiskEngine().evaluate(payload, load())

    assert result["status"] == status
    assert result["metadata"]["risk_score"] == score
    assert "RECIPIENT_FAN_IN" not in result["metadata"]["reason_codes"]
    assert "ACCESS_CONTEXT_CHANGE" in result["metadata"]["reason_codes"]

def test_equal_timestamps_keep_all_receipts_in_the_rolling_peak(history):
    _, add, load = history
    old = NOW - timedelta(days=2)

    for seconds, count in ((0, 4), (300, 3), (601, 2)):
        stamp = old + timedelta(seconds=seconds)
        for _ in range(count):
            add(
                source_document=document(),
                event_at=stamp,
                received_at=stamp,
            )

    recent_receipts(add)

    for profile in load()["recipient_profile"]["clocks"].values():
        assert profile["daily_peaks"][0]["peak_5m"] == 7


def test_equal_event_times_preserve_different_observation_days(history):
    _, add, load = history

    for day in (2, 3, 4):
        add(
            event_at=NOW - timedelta(days=2),
            received_at=NOW - timedelta(days=day),
        )

    recent_receipts(add)
    profile = load()["recipient_profile"]["clocks"]["event"]

    assert len(profile["daily_peaks"]) == 1
    assert profile["daily_peaks"][0]["peak_5m"] == 3
    assert profile["observed_days"] == 3
    assert profile["observed_span_seconds"] == 48 * 3600


def test_warmup_contributes_to_peak_without_training_maturity(history):
    _, add, load = history
    start = NOW - timedelta(days=14)

    for minutes in (-4, 0, 1):
        stamp = start + timedelta(minutes=minutes)
        add(event_at=stamp, received_at=stamp)

    recent_receipts(add)
    profiles = load()["recipient_profile"]["clocks"]

    assert profiles["received"]["daily_peaks"][0]["peak_5m"] == 3
    assert profiles["event"]["daily_peaks"][0]["peak_5m"] == 2

    for profile in profiles.values():
        assert profile["observed_days"] == 1
        assert profile["observed_span_seconds"] == 60