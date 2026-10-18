from copy import deepcopy

import pytest

from rules.accumulation import (
    evaluate_accumulation,
    needs_accumulation_reference,
)


@pytest.fixture
def sample():
    payload = {
        "amount": 90_000,
        "source_account": {
            "owner": {"document_number": "12345678909"}
        },
        "destination_account": {
            "owner": {"document_number": "98765432100"}
        },
    }

    activity = {}

    for window in ("1h", "24h"):
        activity.update({
            f"approved_third_party_count_{window}": 2,
            f"approved_third_party_amount_cents_{window}": 180_000,
            f"approved_third_party_max_cents_{window}": 90_000,
        })

    context = {
        "activity": {"source_received": activity},
        "amount_profile": {
            "assessed": True,
            "sample_count": 12,
            "observed_span_seconds": 172_800,
            "daily_peaks": [
                {
                    "day": f"2026-10-{day:02d}",
                    "peak_adjusted_cents_1h": 20_000,
                    "peak_adjusted_cents_24h": 40_000,
                }
                for day in (12, 13, 14)
            ],
        },
    }

    return payload, context


def test_repeated_small_payments_generate_amount_signals(sample):
    result = evaluate_accumulation(*sample)

    assert {s["code"] for s in result["signals"]} == {
        "ACCUMULATED_AMOUNT_1H",
        "ACCUMULATED_AMOUNT_24H",
    }
    assert (
        result["summary"]["windows"]["1h"]["adjusted_cents"]
        == 180_000
    )
    assert needs_accumulation_reference(*sample) is True


def test_rent_and_two_small_purchases_do_not_trigger(sample):
    payload, context = sample
    payload["amount"] = 150_000

    for window in ("1h", "24h"):
        context["activity"]["source_received"].update({
            f"approved_third_party_amount_cents_{window}": 5_000,
            f"approved_third_party_max_cents_{window}": 3_000,
        })

    assert evaluate_accumulation(*sample)["signals"] == []
    assert needs_accumulation_reference(*sample) is False


def test_habitual_high_volume_uses_its_own_reference(sample):
    for day in sample[1]["amount_profile"]["daily_peaks"]:
        day["peak_adjusted_cents_1h"] = 200_000
        day["peak_adjusted_cents_24h"] = 300_000

    result = evaluate_accumulation(*sample)

    assert result["signals"] == []
    assert (
        result["summary"]["windows"]["1h"]["effective_limit_cents"]
        == 400_000
    )


@pytest.mark.parametrize(
    "amount,trigger", [(49_999, False), (50_000, True)]
)
def test_exact_materiality_boundary(sample, amount, trigger):
    sample[0]["amount"] = amount

    for window in ("1h", "24h"):
        sample[1]["activity"]["source_received"].update({
            f"approved_third_party_amount_cents_{window}": 100_000,
            f"approved_third_party_max_cents_{window}": 50_000,
        })

    assert bool(
        evaluate_accumulation(*sample)["signals"]
    ) is trigger


def test_two_payments_are_not_a_distributed_pattern(sample):
    sample[0]["amount"] = 200_000

    for window in ("1h", "24h"):
        sample[1]["activity"]["source_received"].update({
            f"approved_third_party_count_{window}": 1,
            f"approved_third_party_amount_cents_{window}": 200_000,
            f"approved_third_party_max_cents_{window}": 200_000,
        })

    assert evaluate_accumulation(*sample)["signals"] == []
    assert needs_accumulation_reference(*sample) is False


@pytest.mark.parametrize(
    "missing", ["samples", "days", "span", "all"]
)
def test_immature_reference_does_not_generate_amount_risk(
    sample, missing
):
    profile = sample[1]["amount_profile"]

    if missing == "samples":
        profile["sample_count"] = 11
    if missing == "days":
        profile["daily_peaks"].pop()
    if missing == "span":
        profile["observed_span_seconds"] = 172_799
    if missing == "all":
        profile.clear()

    result = evaluate_accumulation(*sample)

    assert result["signals"] == []
    assert result["summary"]["mature"] is False


def test_self_transfers_do_not_generate_or_train_this_signal(sample):
    sample[0]["destination_account"]["owner"] = deepcopy(
        sample[0]["source_account"]["owner"]
    )

    assert evaluate_accumulation(*sample)["signals"] == []
    assert needs_accumulation_reference(*sample) is False


def test_one_historical_outlier_does_not_inflate_the_reference(
    sample
):
    sample[1]["amount_profile"]["daily_peaks"][-1].update(
        peak_adjusted_cents_1h=1_000_000_000,
        peak_adjusted_cents_24h=1_000_000_000,
    )

    result = evaluate_accumulation(*sample)

    assert len(result["signals"]) == 2
    assert (
        result["summary"]["windows"]["1h"]["effective_limit_cents"]
        == 100_000
    )


def test_daily_accumulation_catches_payments_outside_the_hour(
    sample
):
    sample[1]["activity"]["source_received"].update(
        approved_third_party_count_1h=0,
        approved_third_party_amount_cents_1h=0,
        approved_third_party_max_cents_1h=0,
    )

    result = evaluate_accumulation(*sample)

    assert [s["code"] for s in result["signals"]] == [
        "ACCUMULATED_AMOUNT_24H"
    ]


def test_even_median_keeps_half_cent_precision(sample):
    profile = sample[1]["amount_profile"]

    profile["daily_peaks"] = [
        {
            "peak_adjusted_cents_1h": value,
            "peak_adjusted_cents_24h": value,
        }
        for value in (100_000, 100_001, 100_000, 100_001)
    ]

    sample[0]["amount"] = 100_001

    for window in ("1h", "24h"):
        sample[1]["activity"]["source_received"].update({
            f"approved_third_party_amount_cents_{window}": 200_001,
            f"approved_third_party_max_cents_{window}": 100_001,
        })

    result = evaluate_accumulation(*sample)
    window = result["summary"]["windows"]["1h"]

    assert window["median_daily_peak_cents"] == "100000.5"
    assert window["effective_limit_cents"] == 200_001
    assert len(result["signals"]) == 2