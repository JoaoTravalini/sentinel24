class RiskEngine:
    policy_version = "bootstrap-1"

    def evaluate(self, payload: dict) -> dict:
        # Temporary policy for validating persistence, not the final risk policy.
        return {
            "status": "two_factor_required",
            "metadata": {
                "policy_version": self.policy_version,
                "policy_ready": False,
                "reason_codes": ["POLICY_UNDER_CONSTRUCTION"],
                "explanation": (
                    "Persistence checkpoint; antifraud rules "
                    "are not implemented yet."
                ),
            },
        }