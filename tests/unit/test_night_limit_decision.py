import pytest

from rules.engine import RiskEngine
from rules.night_limit import assess_night_limit
from tests.unit.test_risk_engine import scenario  # noqa: F401


@pytest.mark.parametrize("prior_count,score", [(0, 0), (7, 35)])
def test_exceeded_limit_overrides_approval_or_step_up(
    scenario, prior_count, score
):
    payload, context = scenario
    context["activity"]["source_received"]["count_1m"] = prior_count

    context["night_limit"] = assess_night_limit(
        {"applicable": True, "limit_cents": 100_000},
        payload["amount"],
        95_000,
    )

    result = RiskEngine().evaluate(payload, context)
    metadata = result["metadata"]

    assert result["status"] == "rejected"
    assert metadata["risk_score"] == score
    assert metadata["decision_basis"] == "operational_limit"
    assert metadata["operational_reason_codes"] == [
        "NIGHT_LIMIT_EXCEEDED"
    ]
    assert (
        metadata["operational_checks"]["night_limit"]["exceeded"]
        is True
    )
    assert "NIGHT_LIMIT_EXCEEDED" not in metadata["reason_codes"]


@pytest.mark.parametrize("prior_count,status", [
    (0, "approved"),
    (7, "two_factor_required"),
])
def test_exact_limit_does_not_override_risk(
    scenario, prior_count, status
):
    payload, context = scenario
    context["activity"]["source_received"]["count_1m"] = prior_count

    context["night_limit"] = assess_night_limit(
        {"applicable": True, "limit_cents": 100_000},
        payload["amount"],
        90_000,
    )

    result = RiskEngine().evaluate(payload, context)

    assert result["status"] == status
    assert result["metadata"]["decision_basis"] == "risk_policy"
    assert result["metadata"]["operational_reason_codes"] == []


def test_missing_operational_context_is_not_silently_approved(
    scenario
):
    payload, context = scenario
    del context["night_limit"]

    with pytest.raises(KeyError, match="night_limit"):
        RiskEngine().evaluate(payload, context)