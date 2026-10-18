from sqlalchemy import (
    BigInteger,
    CHAR,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID

from models.base import Base


class PixAnalysis(Base):
    __tablename__ = "pix_analyses"

    id = Column(BigInteger, primary_key=True)
    analysis_key = Column(UUID(as_uuid=True), nullable=False, unique=True)
    pix_id = Column(String(128), nullable=False, unique=True)
    payload_hash = Column(CHAR(64), nullable=False)

    amount = Column(BigInteger, nullable=False)
    transaction_at = Column(DateTime(timezone=True), nullable=False)
    received_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    source_document = Column(String(14), nullable=False)
    destination_document = Column(String(14), nullable=False)
    device_id = Column(String(128), nullable=False)
    source_ip = Column(INET, nullable=False)
    channel = Column(String(32), nullable=False)

    status = Column(String(24), nullable=False)
    policy_version = Column(String(32), nullable=False)

    raw_payload = Column(JSONB, nullable=False)
    decision_metadata = Column(JSONB, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "amount > 0",
            name="pix_analyses_amount_check",
        ),
        CheckConstraint(
            "status IN ('approved', 'two_factor_required', 'rejected')",
            name="pix_analyses_status_check",
        ),
        CheckConstraint(
            "length(btrim(pix_id)) > 0",
            name="pix_analyses_pix_id_check",
        ),
        CheckConstraint(
            "length(btrim(policy_version)) > 0",
            name="pix_analyses_policy_version_check",
        ),
        CheckConstraint(
            "jsonb_typeof(raw_payload) = 'object'",
            name="pix_analyses_raw_payload_check",
        ),
        CheckConstraint(
            "jsonb_typeof(decision_metadata) = 'object'",
            name="pix_analyses_decision_metadata_check",
        ),
        Index(
            "pix_analyses_source_history_idx",
            "source_document",
            "transaction_at",
            "id",
        ),
        Index(
            "pix_analyses_destination_history_idx",
            "destination_document",
            "transaction_at",
            "id",
        ),
        Index(
            "pix_analyses_source_activity_idx",
            "source_document",
            "received_at",
            "id",
        ),
        Index(
            "pix_analyses_destination_activity_idx",
            "destination_document",
            "received_at",
            "id",
        ),
        Index(
            "pix_analyses_device_activity_idx",
            "device_id",
            "received_at",
            "id",
        ),
        Index(
            "pix_analyses_device_context_idx",
            "device_id",
            "received_at",
            postgresql_include=[
                "source_document",
                "status",
                "amount",
                "pix_id",
            ],
        ),
    )