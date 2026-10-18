from copy import deepcopy

import pytest

from rules.recipient import evaluate_recipient


@pytest.fixture
def context():
    return {
        "activity": {
            f"destination_{clock}": {
                "count_5m": 0,
                "counterparties_5m": 0,
            }
            for clock in ("received", "event")
        },
        "recipient_profile": {"clocks": {}},
    }


def add_profile(context, clock="received", peaks=(12, 12, 12)):
    context["recipient_profile"]["clocks"][clock] = {
        "daily_peaks": [{"peak_5m": peak} for peak in peaks],
        "observed_days": 3,
        "observed_span_seconds": 48 * 3600,
    }


def activity(context, count, payers=5, clock="received"):
    context["activity"][f"destination_{clock}"].update(
        count_5m=count,
        counterparties_5m=payers,
    )


@pytest.mark.parametrize("prior,triggered", [(6, False), (7, True)])
def test_without_history_keeps_initial_limit(context, prior, triggered):
    activity(context, prior)
    result = evaluate_recipient(context)

    assert bool(result["signals"]) is triggered
    assert result["summary"]["received"]["effective_limit_5m"] == 8


@pytest.mark.parametrize("payers,triggered", [(4, False), (5, True)])
def test_volume_alone_is_not_diversity(context, payers, triggered):
    activity(context, count=30, payers=payers)

    assert bool(evaluate_recipient(context)["signals"]) is triggered


def test_habitual_receiving_peak_does_not_signal_alone(context):
    for clock in ("received", "event"):
        add_profile(context, clock)
        activity(context, count=11, payers=11, clock=clock)

    result = evaluate_recipient(context)

    assert result["signals"] == []
    assert result["summary"]["received"]["effective_limit_5m"] == 24


def test_same_volume_has_different_meaning_for_different_receivers(context):
    activity(context, count=11)
    quiet = deepcopy(context)
    add_profile(quiet, peaks=(1, 1, 1))
    add_profile(context)

    assert evaluate_recipient(quiet)["signals"]
    assert evaluate_recipient(context)["signals"] == []


@pytest.mark.parametrize("prior,triggered", [(22, False), (23, True)])
def test_growth_counts_current_once(context, prior, triggered):
    add_profile(context)
    activity(context, count=prior)
    result = evaluate_recipient(context)

    assert bool(result["signals"]) is triggered
    if triggered:
        evidence = result["signals"][0]["evidence"]
        assert evidence["count_including_current"] == 24
        assert evidence["effective_limit"] == 24
        assert evidence["limit_source"] == "historical_profile"


@pytest.mark.parametrize("missing", ["days", "observed_days", "span"])
def test_insufficient_observation_keeps_initial_policy(context, missing):
    add_profile(context)
    profile = context["recipient_profile"]["clocks"]["received"]

    if missing == "days":
        profile["daily_peaks"].pop()
    elif missing == "observed_days":
        profile["observed_days"] = 2
    else:
        profile["observed_span_seconds"] = 48 * 3600 - 1

    activity(context, count=7)
    result = evaluate_recipient(context)

    assert result["summary"]["received"]["mature"] is False
    assert result["signals"][0]["evidence"]["effective_limit"] == 8


def test_one_extreme_day_does_not_define_habitual_volume(context):
    add_profile(context, peaks=(8, 8, 1000))
    result = evaluate_recipient(context)

    assert result["summary"]["received"]["effective_limit_5m"] == 16


def test_even_sample_preserves_half_unit_median(context):
    add_profile(context, peaks=(8, 8, 9, 9))
    result = evaluate_recipient(context)

    assert result["summary"]["received"]["median_daily_peak_5m"] == "8.5"
    assert result["summary"]["received"]["effective_limit_5m"] == 17


def test_event_history_cannot_raise_received_limit(context):
    add_profile(context, clock="event", peaks=(100, 100, 100))
    activity(context, count=7)
    result = evaluate_recipient(context)

    assert result["signals"][0]["evidence"]["scope"] == "destination_received"
    assert result["signals"][0]["evidence"]["effective_limit"] == 8


def test_diversity_and_volume_must_belong_to_same_clock(context):
    activity(context, count=7, payers=4, clock="received")
    activity(context, count=6, payers=5, clock="event")

    assert evaluate_recipient(context)["signals"] == []