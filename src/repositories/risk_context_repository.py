from datetime import datetime, timedelta

from sqlalchemy import and_, func, literal, select, union_all

from database import Context
from models import PixAnalysis
from rules.geography import GEOGRAPHY_PARAMETERS


class RiskContextRepository:
    PROFILE_LOOKBACK_DAYS = 30
    PROFILE_SAMPLE_LIMIT = 100
    PROFILE_EXCLUSION_MINUTES = 5

    def __init__(self, context: Context) -> None:
        self.session = context.db_session

    def _activity_query(
        self,
        scope,
        actor_column,
        actor,
        clock_column,
        reference_at,
        observed_at,
        counterpart_column,
        pix_id,
    ):
        minute = clock_column >= reference_at - timedelta(minutes=1)
        five_minutes = clock_column >= reference_at - timedelta(minutes=5)
        hour = clock_column >= reference_at - timedelta(hours=1)

        approved = and_(
            five_minutes,
            PixAnalysis.status == "approved",
        )
        rejected = and_(
            five_minutes,
            PixAnalysis.status == "rejected",
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
            func.count(func.distinct(counterpart_column))
            .filter(five_minutes).label("counterparties_5m"),
            func.count(func.distinct(counterpart_column))
            .filter(hour).label("counterparties_1h"),
            func.count().filter(approved).label("approved_count_5m"),
            amount(approved).label("approved_amount_cents_5m"),
            func.count(func.distinct(counterpart_column))
            .filter(approved).label("approved_counterparties_5m"),
            func.count().filter(rejected).label("rejected_count_5m"),
        ).where(
            actor_column == actor,
            clock_column >= reference_at - timedelta(hours=24),
            clock_column <= reference_at,
            PixAnalysis.received_at <= observed_at,
            PixAnalysis.pix_id != pix_id,
        )

    def load(self, payload: dict, observed_at: datetime) -> dict:
        """Load prior context from normalized input, after acquiring actor locks."""
        event_at = datetime.fromisoformat(payload["transaction_date"])

        source = (
            payload["source_account"]["owner"]["document_number"]
        )
        destination = (
            payload["destination_account"]["owner"]["document_number"]
        )
        device = payload["source"]["device_id"]

        specifications = [
            (
                "source_received",
                PixAnalysis.source_document,
                source,
                PixAnalysis.received_at,
                observed_at,
                PixAnalysis.destination_document,
            ),
            (
                "source_event",
                PixAnalysis.source_document,
                source,
                PixAnalysis.transaction_at,
                event_at,
                PixAnalysis.destination_document,
            ),
            (
                "destination_received",
                PixAnalysis.destination_document,
                destination,
                PixAnalysis.received_at,
                observed_at,
                PixAnalysis.source_document,
            ),
            (
                "destination_event",
                PixAnalysis.destination_document,
                destination,
                PixAnalysis.transaction_at,
                event_at,
                PixAnalysis.source_document,
            ),
            (
                "source_incoming_received",
                PixAnalysis.destination_document,
                source,
                PixAnalysis.received_at,
                observed_at,
                PixAnalysis.source_document,
            ),
            (
                "source_incoming_event",
                PixAnalysis.destination_document,
                source,
                PixAnalysis.transaction_at,
                event_at,
                PixAnalysis.source_document,
            ),
            (
                "device_received",
                PixAnalysis.device_id,
                device,
                PixAnalysis.received_at,
                observed_at,
                PixAnalysis.source_document,
            ),
        ]

        queries = [
            self._activity_query(
                scope,
                actor_column,
                actor,
                clock_column,
                reference,
                observed_at,
                counterpart,
                payload["id"],
            )
            for (
                scope,
                actor_column,
                actor,
                clock_column,
                reference,
                counterpart,
            ) in specifications
        ]

        rows = self.session.execute(
            union_all(*queries)
        ).mappings().all()

        activity = {
            row["scope"]: {
                key: int(value)
                for key, value in row.items()
                if key != "scope"
            }
            for row in rows
        }

        profile = self._source_profile(
            payload["id"],
            source,
            event_at,
            observed_at,
        )

        return {
            "reference": {
                "event_at": event_at.isoformat(),
                "observed_at": observed_at.isoformat(),
                "includes_current": False,
            },
            "activity": activity,
            "source_profile": profile,
            "previous_location": self._previous_location(
                payload,
                observed_at,
            ),
        }

    def _source_profile(
        self,
        pix_id,
        document,
        event_at,
        observed_at,
    ) -> dict:
        rows = (
            self.session.query(
                PixAnalysis.analysis_key,
                PixAnalysis.amount,
                PixAnalysis.transaction_at,
                PixAnalysis.received_at,
                PixAnalysis.destination_document,
                PixAnalysis.device_id,
                PixAnalysis.source_ip,
                PixAnalysis.channel,
                PixAnalysis.raw_payload["source"]["geolocation"]
                .label("geolocation"),
            )
            .filter(
                PixAnalysis.source_document == document,
                PixAnalysis.status == "approved",
                PixAnalysis.transaction_at >= (
                    event_at - timedelta(days=self.PROFILE_LOOKBACK_DAYS)
                ),
                PixAnalysis.transaction_at < (
                    event_at
                    - timedelta(minutes=self.PROFILE_EXCLUSION_MINUTES)
                ),
                PixAnalysis.received_at <= observed_at,
                PixAnalysis.pix_id != pix_id,
            )
            .order_by(
                PixAnalysis.transaction_at.desc(),
                PixAnalysis.id.desc(),
            )
            .limit(self.PROFILE_SAMPLE_LIMIT + 1)
            .all()
        )

        entries = [
            {
                "key": str(row.analysis_key),
                "amount_cents": int(row.amount),
                "transaction_at": row.transaction_at.isoformat(),
                "received_at": row.received_at.isoformat(),
                "destination_document": row.destination_document,
                "device_id": row.device_id,
                "source_ip": str(row.source_ip),
                "channel": row.channel,
                "geolocation": row.geolocation,
            }
            for row in rows[:self.PROFILE_SAMPLE_LIMIT]
        ]

        return {
            "lookback_days": self.PROFILE_LOOKBACK_DAYS,
            "sample_limit": self.PROFILE_SAMPLE_LIMIT,
            "excluded_recent_minutes": self.PROFILE_EXCLUSION_MINUTES,
            "sample_count": len(entries),
            "truncated": len(rows) > self.PROFILE_SAMPLE_LIMIT,
            "entries": entries,
        }

    def _previous_location(
        self,
        payload,
        observed_at,
    ) -> dict | None:
        if payload["source"].get("geolocation") is None:
            return None

        event_at = datetime.fromisoformat(
            payload["transaction_date"]
        )
        document = (
            payload["source_account"]["owner"]
            ["document_number"]
        )

        row = (
            self.session.query(
                PixAnalysis.analysis_key,
                PixAnalysis.transaction_at,
                PixAnalysis.device_id,
                PixAnalysis.raw_payload["source"]["geolocation"]
                .label("geolocation"),
            )
            .filter(
                PixAnalysis.source_document == document,
                PixAnalysis.device_id
                == payload["source"]["device_id"],
                PixAnalysis.status == "approved",
                PixAnalysis.transaction_at
                >= event_at - timedelta(
                    hours=GEOGRAPHY_PARAMETERS[
                        "geo_lookback_hours"
                    ]
                ),
                PixAnalysis.transaction_at <= event_at,
                PixAnalysis.received_at <= observed_at,
                PixAnalysis.pix_id != payload["id"],
                PixAnalysis.raw_payload["source"]["geolocation"]
                .astext.isnot(None),
            )
            .order_by(
                PixAnalysis.transaction_at.desc(),
                PixAnalysis.id.desc(),
            )
            .first()
        )

        if row is None:
            return None

        return {
            "key": str(row.analysis_key),
            "transaction_at": row.transaction_at.isoformat(),
            "device_id": row.device_id,
            "geolocation": row.geolocation,
        }