from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from rules.engine import RiskEngine
from tests.unit.test_risk_engine import scenario


@pytest.fixture
def profiled(scenario):
    payload, context = deepcopy(scenario)

    payload["source"] = {
        "device_id": "known-device",
        "ip": "198.51.100.23",
        "channel": "app",
    }

    now = datetime(
        2026, 10, 17, 18,
        tzinfo=timezone.utc,
    )
    entries = []

    for index in range(12):
        timestamp = (
            now - timedelta(days=2, hours=index * 4)
        ).isoformat()

        entries.append({
            "amount_cents": 10_000,
            "transaction_at": timestamp,
            "received_at": timestamp,
            "device_id": "known-device",
            "source_ip": "198.51.100.23",
            "channel": "app",
            "destination_document": (
                payload["destination_account"]
                ["owner"]["document_number"]
            ),
        })

    context["source_profile"] = {
        "entries": entries,
        "truncated": False,
    }

    return payload, context


def decide(profiled):
    return RiskEngine().evaluate(*profiled)


def test_familiar_behavior_is_approved(profiled):
    result = decide(profiled)

    assert result["status"] == "approved"
    assert result["metadata"]["risk_score"] == 0
    assert (
        result["metadata"]["context"]
        ["source_profile"]["mature"] is True
    )


@pytest.mark.parametrize("amount,spike", [
    (99_999, False),
    (100_000, True),
])
def test_spike_has_a_material_amount_floor(profiled, amount, spike):
    profiled[0]["amount"] = amount
    result = decide(profiled)

    assert result["status"] == "approved"
    assert (
        "PROFILE_AMOUNT_SPIKE" in result["metadata"]["reason_codes"]
    ) is spike
    assert result["metadata"]["risk_score"] == (25 if spike else 0)


@pytest.mark.parametrize("amount,spike", [
    (99_999, False),
    (100_000, True),
])
def test_five_times_median_boundary(profiled, amount, spike):
    for entry in profiled[1]["source_profile"]["entries"]:
        entry["amount_cents"] = 20_000

    profiled[0]["amount"] = amount
    result = decide(profiled)

    assert result["status"] == "approved"
    assert (
        "PROFILE_AMOUNT_SPIKE" in result["metadata"]["reason_codes"]
    ) is spike
    assert result["metadata"]["risk_score"] == (25 if spike else 0)


def test_even_sample_median_preserves_half_cent_exactness(profiled):
    entries = profiled[1]["source_profile"]["entries"]

    for index, entry in enumerate(entries):
        entry["amount_cents"] = 20_000 if index < 6 else 20_001

    profiled[0]["amount"] = 100_002
    result = decide(profiled)

    assert result["status"] == "approved"
    assert result["metadata"]["risk_score"] == 0
    assert "PROFILE_AMOUNT_SPIKE" not in result["metadata"]["reason_codes"]
    assert (
        result["metadata"]["context"]["source_profile"]
        ["median_amount_cents"] == "20000.5"
    )

    profiled[0]["amount"] = 100_003
    result = decide(profiled)

    assert result["status"] == "approved"
    assert result["metadata"]["risk_score"] == 25
    assert "PROFILE_AMOUNT_SPIKE" in result["metadata"]["reason_codes"]


def test_one_outlier_does_not_hide_the_spike(profiled):
    profiled[1]["source_profile"]["entries"][0]["amount_cents"] = 9_000_000
    profiled[0]["amount"] = 100_000
    result = decide(profiled)

    assert result["status"] == "approved"
    assert result["metadata"]["risk_score"] == 25
    assert "PROFILE_AMOUNT_SPIKE" in result["metadata"]["reason_codes"]


def test_insufficient_samples_do_not_prove_a_new_device(profiled):
    profiled[1]["source_profile"]["entries"].pop()
    profiled[0]["source"]["device_id"] = "another-device"
    profiled[0]["amount"] = 100_000

    result = decide(profiled)

    assert result["status"] == "approved"
    assert result["metadata"]["reason_codes"] == []


def test_instant_backfill_does_not_create_a_mature_profile(profiled):
    for entry in profiled[1]["source_profile"]["entries"]:
        entry["received_at"] = "2026-10-17T17:59:50+00:00"

    profiled[0]["amount"] = 100_000
    profiled[1]["activity"]["source_received"]["count_1m"] = 12

    result = decide(profiled)
    codes = result["metadata"]["reason_codes"]

    assert result["status"] == "two_factor_required"
    assert "SOURCE_BURST_1M" in codes
    assert "PROFILE_AMOUNT_SPIKE" not in codes
    assert (
        result["metadata"]["context"]
        ["source_profile"]["mature"] is False
    )


def test_only_two_event_days_are_not_enough(profiled):
    entries = profiled[1]["source_profile"]["entries"]

    for index, entry in enumerate(entries):
        entry["transaction_at"] = (
            "2026-10-15T00:00:00+00:00"
            if index < 6
            else "2026-10-16T00:00:00+00:00"
        )

    profiled[0]["source"]["device_id"] = "another-device"
    result = decide(profiled)

    assert result["status"] == "approved"
    assert (
        "INSUFFICIENT_EVENT_DAYS"
        in result["metadata"]["context"]
        ["source_profile"]["maturity_reasons"]
    )


def test_new_device_alone_is_not_an_automatic_block(profiled):
    profiled[0]["source"]["device_id"] = "another-device"
    result = decide(profiled)

    assert result["status"] == "approved"
    assert result["metadata"]["risk_score"] == 30


def test_combined_access_changes_use_one_family_maximum(profiled):
    profiled[0]["source"].update(
        device_id="another-device",
        ip="203.0.113.99",
        channel="web",
    )

    result = decide(profiled)

    assert result["status"] == "two_factor_required"
    assert result["metadata"]["risk_score"] == 35


@pytest.mark.parametrize("window,prior,score,status", [
    ("count_1m", 7, 60, "two_factor_required"),
    ("count_5m", 19, 70, "rejected"),
])
def test_known_device_and_payee_do_not_override_velocity_and_amount(
    profiled, window, prior, score, status
):
    profiled[0]["amount"] = 100_000
    profiled[1]["activity"]["source_received"][window] = prior
    result = decide(profiled)

    assert result["status"] == status
    assert result["metadata"]["risk_score"] == score
    assert "PROFILE_AMOUNT_SPIKE" in result["metadata"]["reason_codes"]


def test_unusual_value_access_and_payee_combine(profiled):
    profiled[0]["amount"] = 100_000
    profiled[0]["source"].update(
        device_id="another-device",
        ip="203.0.113.99",
    )
    profiled[0]["destination_account"]["owner"][
        "document_number"
    ] = "11111111111"

    result = decide(profiled)

    assert result["status"] == "rejected"
    assert result["metadata"]["risk_score"] == 75


def test_profile_records_are_not_exported(profiled):
    result = decide(profiled)
    summary = result["metadata"]["context"]["source_profile"]

    assert "entries" not in summary
    assert "known-device" not in json.dumps(result)
    assert "198.51.100.23" not in json.dumps(result)


@pytest.mark.parametrize("unknown_payee,score,status", [
    (False, 25, "approved"),
    (True, 40, "two_factor_required"),
])
def test_rent_sized_payment_needs_context_beyond_value(
    profiled, unknown_payee, score, status
):
    # Histórico habitual de R$ 50.
    for entry in profiled[1]["source_profile"]["entries"]:
        entry["amount_cents"] = 5_000

    # Pagamento de R$ 1.500.
    profiled[0]["amount"] = 150_000

    if unknown_payee:
        profiled[0]["destination_account"]["owner"][
            "document_number"
        ] = "11111111111"

    result = decide(profiled)

    assert result["status"] == status
    assert result["metadata"]["risk_score"] == score
    assert "PROFILE_AMOUNT_SPIKE" in result["metadata"]["reason_codes"]
    assert result["metadata"]["policy_version"] == "sentinel24-1.5"


def test_absolute_and_relative_amount_signals_use_family_maximum(profiled):
    profiled[0]["amount"] = 2_000_000
    result = decide(profiled)

    assert result["status"] == "approved"
    assert result["metadata"]["risk_score"] == 30
    assert result["metadata"]["family_scores"] == {
        "transaction_value": 30,
    }
    assert {
        "HIGH_AMOUNT",
        "VERY_HIGH_AMOUNT",
        "PROFILE_AMOUNT_SPIKE",
    } <= set(result["metadata"]["reason_codes"])

@pytest.mark.parametrize("total,score,status", [
    (3, 15, "approved"),
    (6, 45, "two_factor_required"),
])
def test_shared_device_risk_depends_on_unfamiliar_relationships(
    profiled, total, score, status
):
    profiled[1]["activity"]["device_received"][
        "counterparties_1h"
    ] = total

    profiled[1]["device_profile"] = {
        "assessed": True,
        "familiar_payers_1h": 3,
    }

    profiled[0]["destination_account"]["owner"][
        "document_number"
    ] = "11111111111"

    result = decide(profiled)

    assert result["status"] == status
    assert result["metadata"]["risk_score"] == score