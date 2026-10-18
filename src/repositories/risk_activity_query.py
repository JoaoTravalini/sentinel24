from datetime import datetime, timedelta

from sqlalchemy import (
    DateTime,
    and_,
    bindparam,
    func,
    literal,
    select,
    union_all,
)

from models import PixAnalysis


def _scope_query(
    scope, actor_column, actor, clock_column, clock, counterpart
):
    def timestamp(name):
        return bindparam(
            f"{clock}_{name}",
            type_=DateTime(timezone=True),
        )

    minute = clock_column >= timestamp("1m")
    five_minutes = clock_column >= timestamp("5m")
    hour = clock_column >= timestamp("1h")
    third_party = and_(
        PixAnalysis.status == "approved",
        PixAnalysis.source_document != PixAnalysis.destination_document,
    )
    third_party_hour = and_(third_party, hour)

    approved = and_(
        five_minutes, PixAnalysis.status == "approved"
    )
    rejected = and_(
        five_minutes, PixAnalysis.status == "rejected"
    )

    def amount(condition=None):
        total = func.sum(PixAnalysis.amount)
        if condition is not None:
            total = total.filter(condition)
        return func.coalesce(total, 0)

    return select(
        literal(scope).label("scope"),
        func.count().filter(minute).label("count_1m"),
        amount(minute).label("amount_cents_1m"),
        func.count().filter(five_minutes).label("count_5m"),
        amount(five_minutes).label("amount_cents_5m"),
        func.count().filter(hour).label("count_1h"),
        amount(hour).label("amount_cents_1h"),
        func.count().label("count_24h"),
        amount().label("amount_cents_24h"),
        func.count(func.distinct(counterpart))
        .filter(five_minutes).label("counterparties_5m"),
        func.count(func.distinct(counterpart))
        .filter(hour).label("counterparties_1h"),
        func.count().filter(approved)
        .label("approved_count_5m"),
        amount(approved).label("approved_amount_cents_5m"),
        func.count(func.distinct(counterpart))
        .filter(approved).label("approved_counterparties_5m"),
        func.count().filter(third_party_hour)
        .label("approved_third_party_count_1h"),
        amount(third_party_hour)
        .label("approved_third_party_amount_cents_1h"),
        func.coalesce(
            func.max(PixAnalysis.amount).filter(third_party_hour), 0
        ).label("approved_third_party_max_cents_1h"),
        func.count().filter(third_party)
        .label("approved_third_party_count_24h"),
        amount(third_party)
        .label("approved_third_party_amount_cents_24h"),
        func.coalesce(
            func.max(PixAnalysis.amount).filter(third_party), 0
        ).label("approved_third_party_max_cents_24h"),
        func.count().filter(rejected)
        .label("rejected_count_5m"),
    ).where(
        actor_column == bindparam(actor),
        clock_column >= timestamp("24h"),
        clock_column <= timestamp("at"),
        PixAnalysis.received_at <= bindparam(
            "observed_at", type_=DateTime(timezone=True)
        ),
        PixAnalysis.pix_id != bindparam("pix_id"),
    )


# Apenas a estrutura SQL é reutilizada.
ACTIVITY_QUERY = union_all(*[
    _scope_query(*specification)
    for specification in (
        (
            "source_received",
            PixAnalysis.source_document, "source",
            PixAnalysis.received_at, "observed",
            PixAnalysis.destination_document,
        ),
        (
            "source_event",
            PixAnalysis.source_document, "source",
            PixAnalysis.transaction_at, "event",
            PixAnalysis.destination_document,
        ),
        (
            "destination_received",
            PixAnalysis.destination_document, "destination",
            PixAnalysis.received_at, "observed",
            PixAnalysis.source_document,
        ),
        (
            "destination_event",
            PixAnalysis.destination_document, "destination",
            PixAnalysis.transaction_at, "event",
            PixAnalysis.source_document,
        ),
        (
            "source_incoming_received",
            PixAnalysis.destination_document, "source",
            PixAnalysis.received_at, "observed",
            PixAnalysis.source_document,
        ),
        (
            "source_incoming_event",
            PixAnalysis.destination_document, "source",
            PixAnalysis.transaction_at, "event",
            PixAnalysis.source_document,
        ),
        (
            "device_received",
            PixAnalysis.device_id, "device",
            PixAnalysis.received_at, "observed",
            PixAnalysis.source_document,
        ),
    )
])


def activity_parameters(
    payload: dict, observed_at: datetime
) -> dict:
    parameters = {
        "pix_id": payload["id"],
        "source": (
            payload["source_account"]["owner"]["document_number"]
        ),
        "destination": (
            payload["destination_account"]["owner"]["document_number"]
        ),
        "device": payload["source"]["device_id"],
    }

    for clock, reference in (
        ("observed", observed_at),
        (
            "event",
            datetime.fromisoformat(payload["transaction_date"]),
        ),
    ):
        parameters[f"{clock}_at"] = reference
        parameters[f"{clock}_1m"] = reference - timedelta(minutes=1)
        parameters[f"{clock}_5m"] = reference - timedelta(minutes=5)
        parameters[f"{clock}_1h"] = reference - timedelta(hours=1)
        parameters[f"{clock}_24h"] = reference - timedelta(hours=24)

    return parameters