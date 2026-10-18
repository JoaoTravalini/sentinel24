import pytest

from rules.engine import RiskEngine
from tests.unit.test_risk_engine import scenario  # noqa: F401
from tests.unit.test_risk_profile import profiled  # noqa: F401


@pytest.mark.parametrize(
    "new_device,new_ip,new_channel,new_payee,burst,score,status,support",
    [
        (True, False, False, True, False, 75,
         "two_factor_required", []),
        (True, True, False, True, False, 80,
         "rejected", ["ACCESS_CONTEXT_CHANGE"]),
        (True, False, True, True, False, 80,
         "rejected", ["ACCESS_CONTEXT_CHANGE"]),
        (False, True, False, True, False, 55,
         "two_factor_required", []),
        (True, False, False, True, True, 100,
         "rejected", ["SOURCE_BURST_1M"]),
        (True, False, False, False, False, 60,
         "two_factor_required", []),
    ],
    ids=[
        "new-phone-needs-step-up",
        "new-phone-and-ip-can-support-rejection",
        "new-phone-and-channel-can-support-rejection",
        "new-ip-does-not-support-rejection",
        "arrival-burst-still-supports-rejection",
        "known-payee-does-not-bypass-step-up",
    ],
)
def test_rejection_requires_qualifying_support(
    profiled, new_device, new_ip, new_channel,
    new_payee, burst, score, status, support,
):
    payload, context = profiled
    payload["amount"] = 2_000_000

    if new_device:
        payload["source"]["device_id"] = "new-device"
    if new_ip:
        payload["source"]["ip"] = "203.0.113.99"
    if new_channel:
        payload["source"]["channel"] = "web"
    if new_payee:
        payload["destination_account"]["owner"][
            "document_number"
        ] = "11111111111"
    if burst:
        context["activity"]["source_received"]["count_1m"] = 7

    result = RiskEngine().evaluate(payload, context)
    metadata = result["metadata"]

    assert result["status"] == status
    assert metadata["risk_score"] == score
    assert metadata["rejection_support_codes"] == support
    assert metadata["strong_dynamic_signal"] is bool(support)

    if new_device:
        assert (
            "DEVICE_NOT_IN_RECENT_PROFILE"
            in metadata["reason_codes"]
        )