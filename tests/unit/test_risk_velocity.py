from copy import deepcopy

import pytest

from rules.velocity import evaluate_velocity


@pytest.fixture
def context():
    return {
        "activity": {
            "source_received": {
                "count_1m": 0,
                "count_5m": 0,
            },
            "source_event": {
                "count_1m": 0,
                "count_5m": 0,
            },
        },
        "frequency_profile": {
            "clocks": {},
        },
    }


def add_profile(
    context,
    clock="received",
    peaks=(8, 8, 8),
):
    context["frequency_profile"]["clocks"][clock] = {
        "daily_peaks": [
            {
                "peak_1m": peak,
                "peak_5m": peak,
            }
            for peak in peaks
        ],
        "observed_days": 3,
        "observed_span_seconds": 2 * 24 * 3600,
    }


@pytest.mark.parametrize("prior,triggered", [
    (6, False),
    (7, True),
])
def test_without_history_keeps_initial_one_minute_limit(
    context, prior, triggered
):
    context["activity"]["source_received"]["count_1m"] = prior
    result = evaluate_velocity(context)

    assert bool(result["signals"]) is triggered
    assert (
        result["summary"]["received"]["effective_limits"]["1m"]
        == 8
    )


@pytest.mark.parametrize("prior,triggered", [
    (18, False),
    (19, True),
])
def test_without_history_keeps_initial_five_minute_limit(
    context, prior, triggered
):
    context["activity"]["source_received"]["count_5m"] = prior
    result = evaluate_velocity(context)

    assert bool(result["signals"]) is triggered
    assert (
        result["summary"]["received"]["effective_limits"]["5m"]
        == 20
    )


def test_repeated_eight_payment_batches_are_not_unusual_alone(
    context
):
    for clock in ("received", "event"):
        add_profile(context, clock)
        context["activity"][f"source_{clock}"].update(
            count_1m=7,
            count_5m=7,
        )

    result = evaluate_velocity(context)

    assert result["signals"] == []
    assert (
        result["summary"]["received"]["effective_limits"]["1m"]
        == 16
    )


def test_same_count_is_unusual_for_low_activity_but_not_high_activity(
    context
):
    context["activity"]["source_received"]["count_1m"] = 9

    low_activity = deepcopy(context)
    add_profile(low_activity, peaks=(1, 1, 1))
    add_profile(context, peaks=(10, 10, 10))

    low_result = evaluate_velocity(low_activity)
    high_result = evaluate_velocity(context)

    assert low_result["signals"][0]["code"] == "SOURCE_BURST_1M"
    assert high_result["signals"] == []


@pytest.mark.parametrize("prior,triggered", [
    (14, False),
    (15, True),
])
def test_personalized_limit_counts_current_request_once(
    context, prior, triggered
):
    add_profile(context)
    context["activity"]["source_received"]["count_1m"] = prior
    result = evaluate_velocity(context)

    assert bool(result["signals"]) is triggered

    if triggered:
        assert (
            result["signals"][0]["evidence"]["effective_limit"]
            == 16
        )


def test_five_minute_window_also_adapts(context):
    add_profile(context, peaks=(30, 30, 30))
    context["activity"]["source_received"]["count_5m"] = 29

    assert evaluate_velocity(context)["signals"] == []

    context["activity"]["source_received"]["count_5m"] = 59
    result = evaluate_velocity(context)

    assert result["signals"][0]["code"] == "SOURCE_BURST_5M"
    assert result["signals"][0]["evidence"]["effective_limit"] == 60


@pytest.mark.parametrize("change", [
    "active_days",
    "observed_days",
    "observed_span",
])
def test_insufficient_history_does_not_raise_limits(
    context, change
):
    add_profile(context)
    profile = context["frequency_profile"]["clocks"]["received"]

    if change == "active_days":
        profile["daily_peaks"].pop()
    elif change == "observed_days":
        profile["observed_days"] = 1
    else:
        profile["observed_span_seconds"] = 3600

    context["activity"]["source_received"]["count_1m"] = 7
    result = evaluate_velocity(context)

    assert result["summary"]["received"]["mature"] is False
    assert (
        result["signals"][0]["evidence"]["limit_source"]
        == "initial_policy"
    )


def test_one_extreme_day_does_not_raise_the_typical_peak(context):
    add_profile(context, peaks=(8, 8, 1000))
    result = evaluate_velocity(context)

    assert (
        result["summary"]["received"]["effective_limits"]["1m"]
        == 16
    )


def test_event_profile_cannot_raise_the_arrival_time_limit(context):
    add_profile(context, "event", peaks=(100, 100, 100))
    context["activity"]["source_received"]["count_1m"] = 7
    result = evaluate_velocity(context)

    assert (
        result["signals"][0]["evidence"]["scope"]
        == "source_received"
    )
    assert result["signals"][0]["evidence"]["effective_limit"] == 8


def test_even_number_of_daily_peaks_preserves_median(context):
    add_profile(context, peaks=(8, 8, 9, 9))
    result = evaluate_velocity(context)

    assert (
        result["summary"]["received"]["median_daily_peaks"]["1m"]
        == "8.5"
    )
    assert (
        result["summary"]["received"]["effective_limits"]["1m"]
        == 17
    )


def test_personalization_does_not_lower_initial_limits(context):
    add_profile(context, peaks=(1, 1, 1))
    result = evaluate_velocity(context)

    assert result["summary"]["received"]["effective_limits"] == {
        "1m": 8,
        "5m": 20,
    }


def test_same_family_signals_are_kept_as_evidence_for_both_windows(
    context
):
    context["activity"]["source_received"].update(
        count_1m=7,
        count_5m=19,
    )
    result = evaluate_velocity(context)

    assert {
        signal["code"]
        for signal in result["signals"]
    } == {
        "SOURCE_BURST_1M",
        "SOURCE_BURST_5M",
    }