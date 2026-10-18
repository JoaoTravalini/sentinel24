from datetime import datetime, timedelta, timezone
import json

import pytest

from rules.engine import RiskEngine


@pytest.fixture
def scenario():
    now = datetime(2026, 10, 17, 18, tzinfo=timezone.utc)

    def account(document):
        return {
            "owner": {"document_number": document},
            "opening_date": (
                now - timedelta(days=365)
            ).isoformat(),
        }

    payload = {
        "amount": 10_000,
        "transaction_date": now.isoformat(),
        "source_account": account("12345678909"),
        "destination_account": account("98765432100"),
    }

    fields = (
        "count_1m",
        "count_5m",
        "counterparties_1h",
        "counterparties_5m",
        "approved_counterparties_5m",
        "approved_amount_cents_5m",
    )

    scopes = (
        "source_received",
        "source_event",
        "destination_received",
        "destination_event",
        "source_incoming_received",
        "source_incoming_event",
        "device_received",
    )

    context = {
        "reference": {"includes_current": False},
        "night_limit": {
            "applicable": False, "exceeded": False,
            "reason": "OUTSIDE_NIGHT_PERIOD",
        },
        "activity": {
            scope: dict.fromkeys(fields, 0)
            for scope in scopes
        },
        "source_profile": {
            "entries": [{"source_ip": "private-history"}],
        },
    }

    return payload, context


def decide(scenario):
    return RiskEngine().evaluate(*scenario)


def test_cold_start_without_geolocation_is_not_penalized(scenario):
    result = decide(scenario)

    assert result["status"] == "approved"
    assert result["metadata"]["risk_score"] == 0


@pytest.mark.parametrize("amount,score", [
    (499_999, 0),
    (500_000, 20),
    (1_999_999, 20),
    (2_000_000, 30),
])
def test_amount_boundaries_do_not_reject_alone(
    scenario, amount, score
):
    scenario[0]["amount"] = amount
    result = decide(scenario)

    assert result["status"] == "approved"
    assert result["metadata"]["risk_score"] == score


@pytest.mark.parametrize("prior,status", [
    (6, "approved"),
    (7, "two_factor_required"),
])
def test_burst_counts_current_request_once(
    scenario, prior, status
):
    scenario[1]["activity"]["source_received"]["count_1m"] = prior

    assert decide(scenario)["status"] == status


def test_overlapping_windows_and_clocks_do_not_multiply_score(
    scenario
):
    for scope in ("source_received", "source_event"):
        scenario[1]["activity"][scope].update(
            count_1m=8,
            count_5m=20,
        )

    result = decide(scenario)

    assert result["status"] == "two_factor_required"
    assert result["metadata"]["risk_score"] == 45
    assert len(result["metadata"]["reason_codes"]) == 2
    assert len(
        result["metadata"]["signals"][0]["evidence"]
    ) == 2


def test_static_risk_requires_step_up_instead_of_automatic_rejection(
    scenario
):
    payload, _ = scenario
    payload["amount"] = 2_000_000

    for account in ("source_account", "destination_account"):
        payload[account]["opening_date"] = (
            payload["transaction_date"]
        )

    result = decide(scenario)

    assert result["metadata"]["risk_score"] == 70
    assert result["status"] == "two_factor_required"


def test_combined_velocity_value_and_recent_destination_rejects(
    scenario
):
    payload, context = scenario
    payload["amount"] = 500_000
    payload["destination_account"]["opening_date"] = (
        payload["transaction_date"]
    )
    context["activity"]["source_received"]["count_1m"] = 7

    result = decide(scenario)

    assert result["status"] == "rejected"
    assert result["metadata"]["risk_score"] == 80


def test_busy_merchant_or_shared_device_does_not_reject_alone(
    scenario
):
    activity = scenario[1]["activity"]

    activity["destination_received"].update(
        counterparties_5m=5,
        count_5m=7,
    )
    assert decide(scenario)["status"] == "approved"

    activity["destination_received"].update(
        counterparties_5m=0,
        count_5m=0,
    )
    activity["device_received"]["counterparties_1h"] = 3

    assert decide(scenario)["status"] == "approved"


@pytest.mark.parametrize("amount,payers,status", [
    (69_999, 3, "approved"),
    (70_000, 3, "two_factor_required"),
    (70_000, 2, "approved"),
])
def test_forwarding_boundary(
    scenario, amount, payers, status
):
    scenario[0]["amount"] = amount

    scenario[1]["activity"]["source_incoming_received"].update(
        approved_counterparties_5m=payers,
        approved_amount_cents_5m=100_000,
    )

    assert decide(scenario)["status"] == status


def test_forwarding_does_not_combine_incompatible_clock_windows(
    scenario
):
    activity = scenario[1]["activity"]

    activity["source_incoming_received"].update(
        approved_counterparties_5m=3,
        approved_amount_cents_5m=50_000,
    )
    activity["source_incoming_event"].update(
        approved_counterparties_5m=1,
        approved_amount_cents_5m=100_000,
    )
    scenario[0]["amount"] = 70_000

    assert decide(scenario)["status"] == "approved"


def test_self_ownership_does_not_disable_velocity_analysis(
    scenario
):
    payload, context = scenario

    payload["destination_account"]["owner"] = dict(
        payload["source_account"]["owner"]
    )
    context["activity"]["source_received"]["count_1m"] = 7

    assert decide(scenario)["status"] == "two_factor_required"


def test_opening_after_transaction_requires_step_up(scenario):
    scenario[0]["source_account"]["opening_date"] = (
        "2026-10-18T18:00:00+00:00"
    )

    assert decide(scenario)["status"] == "two_factor_required"


def test_metadata_is_serializable_without_exporting_profile_records(
    scenario
):
    serialized = json.dumps(decide(scenario))

    assert "private-history" not in serialized
    assert "POLICY_UNDER_CONSTRUCTION" not in serialized