from datetime import datetime, timedelta

import pytest

from rules.engine import RiskEngine
from tests.unit.test_risk_engine import scenario  # noqa: F401
from tests.unit.test_risk_geography import located  # noqa: F401


def test_geography_and_velocity_can_support_rejection(located):
    _, context = located
    context["activity"]["source_received"]["count_1m"] = 7

    result = RiskEngine().evaluate(*located)

    assert result["status"] == "rejected"
    assert result["metadata"]["risk_score"] == 70
    assert result["metadata"]["rejection_support_codes"] == [
        "SOURCE_BURST_1M",
    ]


@pytest.mark.parametrize("new_device,status,support", [
    (False, "two_factor_required", []),
    (True, "rejected", ["DEVICE_NOT_IN_RECENT_PROFILE"]),
])
def test_geography_requires_qualifying_access_evidence(
    located, new_device, status, support
):
    payload, context = located
    event_at = datetime.fromisoformat(
        payload["transaction_date"]
    )

    payload["amount"] = 500_000
    payload["destination_account"]["opening_date"] = (
        payload["transaction_date"]
    )
    payload["source"].update(
        ip="203.0.113.99",
        channel="app",
    )

    destination = (
        payload["destination_account"]["owner"]["document_number"]
    )
    entries = []

    for index in range(12):
        stamp = event_at - timedelta(
            days=2, hours=index * 4
        )

        entries.append({
            "amount_cents": 500_000,
            "transaction_at": stamp.isoformat(),
            "received_at": stamp.isoformat(),
            "device_id": (
                "old-device" if new_device else "same-device"
            ),
            "source_ip": (
                "203.0.113.99"
                if new_device
                else "198.51.100.23"
            ),
            "channel": "app",
            "destination_document": destination,
        })

    context["source_profile"]["entries"] = entries

    result = RiskEngine().evaluate(*located)
    metadata = result["metadata"]

    assert metadata["context"]["source_profile"]["mature"] is True
    assert metadata["risk_score"] == 80
    assert result["status"] == status
    assert metadata["rejection_support_codes"] == support
    assert metadata["strong_dynamic_signal"] is bool(support)