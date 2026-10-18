from datetime import timedelta

from rules.engine import RiskEngine
from rules.velocity import evaluate_velocity
from tests.integration.test_risk_context import NOW, document, history


def add_daily_batches(add, count=8):
    for day in (2, 3, 4):
        for index in range(count):
            timestamp = NOW - timedelta(
                days=day,
                seconds=index * 5,
            )
            add(
                event_at=timestamp,
                received_at=timestamp,
            )


def test_database_builds_rolling_daily_peaks(history):
    _, add, load = history
    add_daily_batches(add)
    context = load()

    for clock in ("received", "event"):
        profile = context["frequency_profile"]["clocks"][clock]

        assert len(profile["daily_peaks"]) == 3
        assert profile["observed_days"] == 3
        assert profile["observed_span_seconds"] >= 48 * 3600
        assert [
            day["peak_1m"]
            for day in profile["daily_peaks"]
        ] == [8, 8, 8]
        assert [
            day["peak_5m"]
            for day in profile["daily_peaks"]
        ] == [8, 8, 8]


def test_frequency_reference_excludes_ineligible_records(history):
    payload, add, load = history
    old = NOW - timedelta(days=2)

    add(event_at=old, received_at=old)
    add(event_at=old, received_at=old, status="rejected")
    add(
        event_at=old,
        received_at=old,
        status="two_factor_required",
    )
    add(
        event_at=old,
        received_at=old,
        source_document=document(),
    )
    add(
        event_at=old,
        received_at=old,
        device_id="another-device",
    )
    add(
        event_at=old,
        received_at=NOW - timedelta(hours=1),
    )
    add(
        event_at=old,
        received_at=NOW - timedelta(days=15),
    )
    add()

    expired = NOW - timedelta(days=15)
    add(event_at=expired, received_at=expired)

    current = add(event_at=old, received_at=old)
    current.pix_id = payload["id"]

    for profile in load()["frequency_profile"]["clocks"].values():
        assert len(profile["daily_peaks"]) == 1
        assert profile["daily_peaks"][0]["peak_1m"] == 1
        assert profile["daily_peaks"][0]["peak_5m"] == 1


def test_backdated_payloads_received_recently_cannot_train_frequency(
    history
):
    _, add, load = history

    for day in (2, 3, 4):
        add(
            event_at=NOW - timedelta(days=day),
            received_at=NOW - timedelta(hours=1),
        )

    context = load()

    assert all(
        profile["daily_peaks"] == []
        for profile in context["frequency_profile"]["clocks"].values()
    )


def test_old_event_dates_do_not_replace_observed_history(history):
    _, add, load = history

    for day in (2, 3, 4):
        add(
            event_at=NOW - timedelta(days=day),
            received_at=NOW - timedelta(days=2),
        )

    result = evaluate_velocity(load())
    profile = result["summary"]["event"]

    assert profile["active_days"] == 3
    assert profile["observed_days"] == 1
    assert profile["mature"] is False
    assert profile["effective_limits"]["1m"] == 8


def test_another_device_does_not_borrow_the_personalized_limit(
    history
):
    payload, add, load = history
    add_daily_batches(add)
    payload["source"]["device_id"] = "new-device"

    result = evaluate_velocity(load())

    for profile in result["summary"].values():
        assert profile["mature"] is False
        assert profile["effective_limits"]["1m"] == 8


def test_rolling_windows_include_the_exact_minute_boundary(history):
    _, add, load = history
    start = NOW - timedelta(days=2)

    for seconds in (0, 60, 121):
        timestamp = start + timedelta(seconds=seconds)
        add(
            event_at=timestamp,
            received_at=timestamp,
        )

    for profile in load()["frequency_profile"]["clocks"].values():
        day = profile["daily_peaks"][0]

        assert day["peak_1m"] == 2
        assert day["peak_5m"] == 3


def test_normal_batch_is_approved_but_growth_still_requires_step_up(
    history
):
    payload, add, load = history
    add_daily_batches(add)

    for _ in range(7):
        add()

    normal = RiskEngine().evaluate(payload, load())

    assert normal["status"] == "approved"
    assert normal["metadata"]["risk_score"] == 0

    reference = (
        normal["metadata"]["context"]
        ["frequency_profile"]["received"]
    )
    assert reference["effective_limits"]["1m"] == 16

    for _ in range(8):
        add()

    unusual = RiskEngine().evaluate(payload, load())

    assert unusual["status"] == "two_factor_required"
    assert unusual["metadata"]["risk_score"] == 35
    assert unusual["metadata"]["reason_codes"] == [
        "SOURCE_BURST_1M",
    ]


def test_familiar_frequency_does_not_override_other_risk(history):
    payload, add, load = history
    add_daily_batches(add)

    for _ in range(7):
        add()

    payload["amount"] = 150_000
    payload["destination_account"]["owner"]["document_number"] = (
        document()
    )
    payload["source"]["ip"] = "203.0.113.99"

    result = RiskEngine().evaluate(payload, load())

    assert result["status"] == "two_factor_required"
    assert result["metadata"]["risk_score"] == 50
    assert (
        "SOURCE_BURST_1M"
        not in result["metadata"]["reason_codes"]
    )
    assert (
        "PROFILE_AMOUNT_SPIKE"
        in result["metadata"]["reason_codes"]
    )