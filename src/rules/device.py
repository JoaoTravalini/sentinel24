DEVICE_PARAMETERS = {
    "device_prior_payers_1h": 3,
    "device_history_days": 14,
    "device_history_exclusion_hours": 24,
    "device_relationship_minimum_days": 3,
}


DEVICE_RULES = {
    "SHARED_DEVICE": ("access_context", 30),
}


def evaluate_device(
    context: dict,
    parameters: dict | None = None,
) -> dict:
    p = {**DEVICE_PARAMETERS, **(parameters or {})}

    total = (
        context["activity"]["device_received"]["counterparties_1h"]
    )
    profile = context.get("device_profile", {})
    assessed = profile.get("assessed", False)

    familiar = (
        profile["familiar_payers_1h"]
        if assessed
        else 0
    )
    unfamiliar = total - familiar

    summary = {
        "assessed": assessed,
        "prior_distinct_payers_1h": total,
        "familiar_payers_1h": familiar,
        "unfamiliar_payers_1h": unfamiliar,
        "trigger_count": p["device_prior_payers_1h"],
        "reference": (
            "established_relationships"
            if assessed
            else "initial_policy"
        ),
    }

    signals = []

    if unfamiliar >= p["device_prior_payers_1h"]:
        signals.append({
            "code": "SHARED_DEVICE",
            "evidence": dict(summary),
        })

    return {
        "summary": summary,
        "signals": signals,
    }