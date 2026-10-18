from datetime import datetime

from rules.profile import (
    PROFILE_PARAMETERS,
    PROFILE_RULES,
    evaluate_profile,
)

from rules.geography import (
    GEOGRAPHY_PARAMETERS,
    GEOGRAPHY_RULES,
    evaluate_geography,
)

from rules.recipient import (
    RECIPIENT_PARAMETERS,
    RECIPIENT_RULES,
    evaluate_recipient,
)

from rules.velocity import FREQUENCY_PARAMETERS, evaluate_velocity
from rules.device import DEVICE_PARAMETERS, DEVICE_RULES, evaluate_device

PARAMETERS = {
    **PROFILE_PARAMETERS,
    **GEOGRAPHY_PARAMETERS,
    **DEVICE_PARAMETERS,
    **FREQUENCY_PARAMETERS,
    **RECIPIENT_PARAMETERS,
    "two_factor_score": 35,
    "rejection_score": 70,
    "minimum_rejection_families": 2,
    "strong_dynamic_points": 30,
    "high_amount_cents": 500_000,
    "very_high_amount_cents": 2_000_000,
    "source_recent_hours": 168,
    "destination_recent_hours": 24,
    "forwarding_prior_payers_5m": 3,
    "forwarding_minimum_cents": 100_000,
    "forwarding_percent": 70,
}



RULES = {
    **PROFILE_RULES,
    **GEOGRAPHY_RULES,
    **DEVICE_RULES,
    **RECIPIENT_RULES,
    "SOURCE_BURST_1M": ("velocity", 35),
    "SOURCE_BURST_5M": ("velocity", 45),
    "HIGH_AMOUNT": ("transaction_value", 20),
    "VERY_HIGH_AMOUNT": ("transaction_value", 30),
    "RECENT_SOURCE_ACCOUNT": ("access_context", 15),
    "RECENT_DESTINATION_ACCOUNT": ("counterparty", 25),
    "RAPID_FORWARDING_PATTERN": ("network_flow", 45),
    "ACCOUNT_TIME_INCONSISTENCY": ("data_quality", 35),
}


class RiskEngine:
    policy_version = "sentinel24-1.6"

    def evaluate(self, payload: dict, context: dict) -> dict:
        p = PARAMETERS
        signals = {}
        activity = context["activity"]
        amount = payload["amount"]
        event_at = datetime.fromisoformat(payload["transaction_date"])

        same_owner = (
            payload["source_account"]["owner"]["document_number"]
            == payload["destination_account"]["owner"]["document_number"]
        )

        def add(code, evidence):
            if code not in signals:
                family, points = RULES[code]
                signals[code] = {
                    "code": code,
                    "family": family,
                    "points": points,
                    "evidence": [],
                }

            signals[code]["evidence"].append(evidence)

        # Analisa o horário informado e o horário observado pelo servidor.
        # A solicitação atual ainda não está no histórico: acrescentamos 1.
        velocity = evaluate_velocity(context, p)

        for signal in velocity["signals"]:
            add(signal["code"], signal["evidence"])

        for threshold, code in (
            (p["high_amount_cents"], "HIGH_AMOUNT"),
            (p["very_high_amount_cents"], "VERY_HIGH_AMOUNT"),
        ):
            if amount >= threshold:
                add(code, {"amount_cents": amount})

        for account, hours, code in (
            (
                "source_account",
                p["source_recent_hours"],
                "RECENT_SOURCE_ACCOUNT",
            ),
            (
                "destination_account",
                p["destination_recent_hours"],
                "RECENT_DESTINATION_ACCOUNT",
            ),
        ):
            opened_at = datetime.fromisoformat(
                payload[account]["opening_date"]
            )
            age_seconds = (event_at - opened_at).total_seconds()

            if age_seconds < 0:
                add("ACCOUNT_TIME_INCONSISTENCY", {
                    "account": account,
                })
            elif age_seconds < hours * 3600:
                if account == "source_account" or not same_owner:
                    add(code, {
                        "account": account,
                        "age_seconds": age_seconds,
                    })

        device = evaluate_device(context, p)

        for signal in device["signals"]:
            add(signal["code"], signal["evidence"])

        recipient = evaluate_recipient(context, p)

        if not same_owner:
            for signal in recipient["signals"]:
                add(signal["code"], signal["evidence"])

            for clock in ("received", "event"):
                incoming = activity[f"source_incoming_{clock}"]
                total = incoming["approved_amount_cents_5m"]

                if (
                    incoming["approved_counterparties_5m"]
                    >= p["forwarding_prior_payers_5m"]
                    and total >= p["forwarding_minimum_cents"]
                    and amount * 100
                    >= total * p["forwarding_percent"]
                ):
                    add("RAPID_FORWARDING_PATTERN", {
                        "scope": f"source_incoming_{clock}",
                        "prior_approved_payers": (
                            incoming["approved_counterparties_5m"]
                        ),
                        "prior_approved_amount_cents": total,
                        "outgoing_amount_cents": amount,
                    })

        # Dentro de uma família, somente o maior peso contribui.
        profile = evaluate_profile(payload, context)

        for signal in profile["signals"]:
            add(signal["code"], signal["evidence"])

        geography = evaluate_geography(payload, context)

        for signal in geography["signals"]:
            add(signal["code"], signal["evidence"])

        families = {}

        for signal in signals.values():
            family = signal["family"]
            families[family] = max(
                families.get(family, 0),
                signal["points"],
            )

        score = min(100, sum(families.values()))

        strong_dynamic = any(
            families.get(family, 0) >= p["strong_dynamic_points"]
            for family in (
                "velocity",
                "access_context",
                "network_flow",
            )
        )

        can_reject = (
            score >= p["rejection_score"]
            and len(families) >= p["minimum_rejection_families"]
            and strong_dynamic
        )

        status = "approved"

        if can_reject:
            status = "rejected"
        elif score >= p["two_factor_score"]:
            status = "two_factor_required"

        return {
            "status": status,
            "metadata": {
                "policy_version": self.policy_version,
                "policy_ready": True,
                "risk_score": score,
                "score_kind": "heuristic_indicator_not_probability",
                "reason_codes": list(signals),
                "signals": list(signals.values()),
                "family_scores": families,
                "strong_dynamic_signal": strong_dynamic,
                "parameters": dict(p),
                "context": {
                    "reference": context["reference"],
                    "activity": activity,
                    "source_profile": profile["summary"],
                    "frequency_profile": velocity["summary"],
                    "device_profile": device["summary"],
                    "recipient_profile": recipient["summary"],
                    "geography": geography["summary"],
                },
            },
        }