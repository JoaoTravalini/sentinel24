import hashlib
from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select, text

from database import Context
from models import PixAnalysis


class PixAnalysisRepository:
    def __init__(self, context: Context) -> None:
        self.session = context.db_session

    def configure_transaction(self) -> None:
        self.session.execute(
            text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
        )
        self.session.execute(
            text("SELECT set_config('statement_timeout', :timeout, true)"),
            {"timeout": "1500ms"},
        )

    def get_by_pix_id(self, pix_id: str) -> PixAnalysis | None:
        return (
            self.session.query(PixAnalysis)
            .filter(PixAnalysis.pix_id == pix_id)
            .first()
        )

    def lock_analysis_inputs(self, payload: dict) -> None:
        source_document = (
            payload["source_account"]["owner"]["document_number"]
        )
        destination_document = (
            payload["destination_account"]["owner"]["document_number"]
        )

        names = {
            f"pix:{payload['id']}",
            f"party:{source_document}",
            f"party:{destination_document}",
            f"device:{payload['source']['device_id']}",
        }

        lock_ids = sorted({
            int.from_bytes(
                hashlib.sha256(name.encode("utf-8")).digest()[:8],
                byteorder="big",
                signed=True,
            )
            for name in names
        })

        self.session.execute(
            text(
                "SELECT pg_advisory_xact_lock(lock_id) "
                "FROM unnest(CAST(:lock_ids AS BIGINT[])) AS locks(lock_id) "
                "ORDER BY lock_id"
            ),
            {"lock_ids": lock_ids},
        ).all()

    def database_time(self) -> datetime:
        return self.session.execute(
            select(func.clock_timestamp())
        ).scalar_one()

    def create(
        self,
        raw_payload: dict,
        normalized: dict,
        payload_hash: str,
        decision: dict,
        received_at: datetime,
    ) -> PixAnalysis:
        analysis = PixAnalysis(
            analysis_key=uuid4(),
            pix_id=normalized["id"],
            payload_hash=payload_hash,
            amount=normalized["amount"],
            transaction_at=datetime.fromisoformat(
                normalized["transaction_date"]
            ),
            received_at=received_at,
            source_document=(
                normalized["source_account"]["owner"]["document_number"]
            ),
            destination_document=(
                normalized["destination_account"]["owner"]["document_number"]
            ),
            device_id=normalized["source"]["device_id"],
            source_ip=normalized["source"]["ip"],
            channel=normalized["source"]["channel"],
            status=decision["status"],
            policy_version=decision["metadata"]["policy_version"],
            raw_payload=raw_payload,
            decision_metadata=decision["metadata"],
        )

        self.session.add(analysis)
        self.session.flush()

        return analysis