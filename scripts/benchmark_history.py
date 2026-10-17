from copy import deepcopy
from datetime import datetime, timedelta
import os
from uuid import uuid4

from sqlalchemy import insert


os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://bootcamp:bootcamp@127.0.0.1:"
    f"{os.environ.get('DB_PORT', '5432')}/bootcamp",
)

from database import SessionLocal
from models import PixAnalysis
from utils.pix_payload import (
    calculate_payload_hash,
    prepare_pix_payload,
)


LOCATION = {
    "latitude": -23.5505,
    "longitude": -46.6333,
}


def context_ready(metadata):
    context = metadata.get("context")

    if not isinstance(context, dict):
        return False

    profile = context.get("source_profile")
    geography = context.get("geography")

    return (
        isinstance(profile, dict)
        and profile.get("mature") is True
        and isinstance(geography, dict)
        and geography.get("assessed") is True
    )


def prepare_history(payloads, depth):
    samples = {}

    for payload in payloads:
        payload["source"]["geolocation"] = dict(LOCATION)

        document = (
            payload["source_account"]["owner"]
            ["document_number"]
        )
        samples.setdefault(document, payload)

    rows = []

    for sample in samples.values():
        event_at = datetime.fromisoformat(
            sample["transaction_date"]
        )

        for index in range(depth):
            timestamp = event_at - timedelta(
                hours=1 + index * 6,
            )

            raw = deepcopy(sample)
            raw["id"] = f"bench-history-{uuid4().hex}"
            raw["transaction_date"] = timestamp.isoformat()

            normalized = prepare_pix_payload(raw)

            rows.append({
                "analysis_key": uuid4(),
                "pix_id": raw["id"],
                "payload_hash": calculate_payload_hash(
                    normalized
                ),
                "amount": normalized["amount"],
                "transaction_at": timestamp,
                "received_at": timestamp,
                "source_document": (
                    normalized["source_account"]
                    ["owner"]["document_number"]
                ),
                "destination_document": (
                    normalized["destination_account"]
                    ["owner"]["document_number"]
                ),
                "device_id": (
                    normalized["source"]["device_id"]
                ),
                "source_ip": normalized["source"]["ip"],
                "channel": normalized["source"]["channel"],
                "status": "approved",
                "policy_version": "benchmark-fixture-1",
                "raw_payload": raw,
                "decision_metadata": {
                    "synthetic": True,
                    "purpose": "load-test-history",
                },
            })

    with SessionLocal.begin() as session:
        session.execute(insert(PixAnalysis), rows)

    return len(rows)