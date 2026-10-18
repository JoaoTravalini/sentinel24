from datetime import datetime, timedelta, timezone


BRASILIA = timezone(timedelta(hours=-3))
DEFAULT_NIGHT_POLICY = {"limit_cents": 100_000, "start_hour": 20}

# Configurações já autorizadas pela instituição; nunca vêm do payload.
# Chave: (documento normalizado, agência, número da conta de origem).
ACCOUNT_NIGHT_POLICIES = {}


def source_account_key(payload: dict) -> tuple[str, str, str]:
    account = payload["source_account"]
    return (
        account["owner"]["document_number"],
        account["branch"].strip(),
        account["account_number"].strip(),
    )


def night_period(
    observed_at: datetime, start_hour: int = 20
) -> tuple[datetime, datetime] | None:
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must include a timezone")
    if type(start_hour) is not int or start_hour not in (20, 22):
        raise ValueError("start_hour must be 20 or 22")

    local = observed_at.astimezone(BRASILIA)
    start = local.replace(
        hour=start_hour, minute=0, second=0, microsecond=0
    )

    if local.hour < 6:
        start -= timedelta(days=1)
    elif local.hour < start_hour:
        return None

    end = (start + timedelta(days=1)).replace(hour=6)

    return (
        start.astimezone(timezone.utc),
        end.astimezone(timezone.utc),
    )


def night_limit_scope(payload: dict, observed_at: datetime) -> dict:
    policy = {
        **DEFAULT_NIGHT_POLICY,
        **ACCOUNT_NIGHT_POLICIES.get(source_account_key(payload), {}),
    }

    limit = policy["limit_cents"]
    if (
        type(limit) is not int
        or not 0 <= limit <= 9_223_372_036_854_775_807
    ):
        raise ValueError("limit_cents must be a nonnegative BIGINT")

    period = night_period(observed_at, policy["start_hour"])

    result = {
        "applicable": False,
        "limit_cents": limit,
        "start_hour": policy["start_hour"],
        "end_hour": 6,
        "timezone": "UTC-03:00",
        "clock": "server_observed_at",
        "usage_basis": "prior_approved_analyses",
        "simulation": True,
    }

    for field, reason in (
        ("source_account", "NON_PF_PAYER"),
        ("destination_account", "NON_PF_RECIPIENT"),
    ):
        document = payload[field]["owner"]["document_number"]

        if (
            len(document) != 11
            or not document.isascii()
            or not document.isdigit()
        ):
            return {**result, "reason": reason}

    if period is None:
        return {**result, "reason": "OUTSIDE_NIGHT_PERIOD"}

    return {
        **result,
        "applicable": True,
        "reason": "NIGHT_PERIOD",
        "period_start": period[0].isoformat(),
        "period_end": period[1].isoformat(),
    }


def assess_night_limit(
    scope: dict, amount_cents: int, prior_approved_cents: int
) -> dict:
    if type(amount_cents) is not int or amount_cents <= 0:
        raise ValueError("amount_cents must be a positive integer")

    if (
        type(prior_approved_cents) is not int
        or prior_approved_cents < 0
    ):
        raise ValueError(
            "prior_approved_cents must be a nonnegative integer"
        )

    if not scope["applicable"]:
        return {**scope, "exceeded": False}

    limit = scope["limit_cents"]
    projected = prior_approved_cents + amount_cents
    exceeded = projected > limit

    return {
        **scope,
        "consumed_cents": prior_approved_cents,
        "remaining_cents": max(0, limit - prior_approved_cents),
        "projected_cents": projected,
        "exceeded": exceeded,
        "reason": (
            "NIGHT_LIMIT_EXCEEDED"
            if exceeded
            else "WITHIN_NIGHT_LIMIT"
        ),
    }