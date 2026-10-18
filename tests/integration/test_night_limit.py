from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from database import SessionLocal, clear_context, open_context
from models import PixAnalysis
from repositories.pix_analysis_repository import PixAnalysisRepository
from resources.pix_analysis import PixAnalysisResource
from rules.night_limit import (
    ACCOUNT_NIGHT_POLICIES,
    source_account_key,
)
from tests.integration.test_pix_analysis import payload  # noqa: F401
from utils.pix_payload import calculate_payload_hash, prepare_pix_payload


@pytest.fixture
def clock(monkeypatch):
    state = {
        "now": datetime.fromisoformat(
            "2026-10-18T01:00:00-03:00"
        )
    }
    monkeypatch.setattr(
        PixAnalysisRepository,
        "database_time",
        lambda self: state["now"],
    )
    return state


@pytest.fixture
def night_payload(payload, clock):
    item = deepcopy(payload)
    item["amount"] = 10_000
    item["transaction_date"] = clock["now"].isoformat()

    for field in ("source_account", "destination_account"):
        item[field]["owner"]["document_number"] = (
            f"{uuid4().int % 100_000_000_000:011d}"
        )
        item[field]["opening_date"] = (
            clock["now"] - timedelta(days=365)
        ).isoformat()

    return item


def fresh(item, amount, clock):
    result = deepcopy(item)
    result["id"] = f"night-{uuid4().hex}"
    result["amount"] = amount
    result["transaction_date"] = clock["now"].isoformat()
    return result


def post(item):
    context = open_context()

    try:
        response = PixAnalysisResource().on_post(
            payload=deepcopy(item)
        )
        return response.status_code, json.loads(response.body)
    finally:
        if context.db_session is not None:
            context.db_session.close()
        clear_context()


def seed(item, at, status="approved"):
    raw = deepcopy(item)
    raw["id"] = f"night-seed-{uuid4().hex}"
    raw["transaction_date"] = at.isoformat()
    normalized = prepare_pix_payload(raw)

    with SessionLocal.begin() as session:
        repository = PixAnalysisRepository(
            SimpleNamespace(db_session=session)
        )
        repository.create(
            raw_payload=raw,
            normalized=normalized,
            payload_hash=calculate_payload_hash(normalized),
            decision={
                "status": status,
                "metadata": {
                    "policy_version": "night-fixture",
                    "synthetic": True,
                },
            },
            received_at=at,
        )


def check(body):
    return body["metadata"]["operational_checks"]["night_limit"]


def test_cumulative_limit_retries_and_saved_rejection(
    night_payload, clock
):
    first = fresh(night_payload, 60_000, clock)
    code, original = post(first)

    assert code == 201
    assert original["status"] == "approved"

    code, repeated = post(first)

    assert code == 200
    assert repeated == original

    code, exact = post(fresh(night_payload, 40_000, clock))

    assert code == 201
    assert exact["status"] == "approved"
    assert check(exact)["consumed_cents"] == 60_000

    last = fresh(night_payload, 1, clock)
    code, rejected = post(last)

    assert code == 201
    assert rejected["status"] == "rejected"
    assert rejected["metadata"]["risk_score"] == 0
    assert (
        rejected["metadata"]["decision_basis"]
        == "operational_limit"
    )
    assert check(rejected)["consumed_cents"] == 100_000

    with SessionLocal() as session:
        saved = (
            session.query(PixAnalysis)
            .filter_by(pix_id=last["id"])
            .one()
        )
        assert saved.status == "rejected"
        assert saved.decision_metadata == rejected["metadata"]


@pytest.mark.parametrize("field", ["branch", "account_number"])
def test_same_owner_other_account_does_not_consume_limit(
    night_payload, clock, field
):
    other = fresh(night_payload, 95_000, clock)
    other["source_account"][field] = "99999"
    seed(other, clock["now"] - timedelta(minutes=2))

    code, result = post(fresh(night_payload, 10_000, clock))

    assert code == 201
    assert result["status"] == "approved"
    assert check(result)["consumed_cents"] == 0


@pytest.mark.parametrize(
    "kind", ["rejected", "two_factor_required", "cnpj"]
)
def test_ineligible_history_does_not_consume_limit(
    night_payload, clock, kind
):
    previous = fresh(night_payload, 95_000, clock)
    status = kind

    if kind == "cnpj":
        status = "approved"
        previous["destination_account"]["owner"][
            "document_number"
        ] = "12345678000190"

    seed(previous, clock["now"] - timedelta(minutes=2), status)

    code, result = post(fresh(night_payload, 10_000, clock))

    assert code == 201
    assert result["status"] == "approved"
    assert check(result)["consumed_cents"] == 0


def test_midnight_preserves_usage_and_next_night_starts_fresh(
    night_payload, clock
):
    clock["now"] = datetime.fromisoformat(
        "2026-10-17T23:59:00-03:00"
    )
    _, first = post(fresh(night_payload, 60_000, clock))
    assert first["status"] == "approved"

    clock["now"] = datetime.fromisoformat(
        "2026-10-18T00:01:00-03:00"
    )
    _, result = post(fresh(night_payload, 50_000, clock))

    assert result["status"] == "rejected"
    assert check(result)["consumed_cents"] == 60_000

    clock["now"] = datetime.fromisoformat(
        "2026-10-18T20:00:00-03:00"
    )
    _, result = post(fresh(night_payload, 50_000, clock))

    assert result["status"] == "approved"
    assert check(result)["consumed_cents"] == 0


def test_payload_date_does_not_bypass_observed_night(
    night_payload, clock
):
    item = fresh(night_payload, 100_001, clock)
    item["transaction_date"] = "2026-10-18T15:00:00-03:00"

    code, result = post(item)

    assert code == 201
    assert result["status"] == "rejected"
    assert check(result)["clock"] == "server_observed_at"
    assert check(result)["exceeded"] is True


def test_concurrent_approvals_cannot_exceed_limit(
    night_payload, clock
):
    items = [
        fresh(night_payload, 30_000, clock)
        for _ in range(4)
    ]

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(post, items))

    assert [code for code, _ in results] == [201] * 4

    bodies = [body for _, body in results]

    assert sum(
        body["status"] == "approved" for body in bodies
    ) == 3

    rejected = [
        body for body in bodies
        if body["status"] == "rejected"
    ]

    assert len(rejected) == 1
    assert check(rejected[0])["consumed_cents"] == 90_000
    assert check(rejected[0])["projected_cents"] == 120_000


def test_authorized_configuration_changes_start_and_limit(
    night_payload, clock, monkeypatch
):
    monkeypatch.setitem(
        ACCOUNT_NIGHT_POLICIES,
        source_account_key(night_payload),
        {"start_hour": 22, "limit_cents": 250_000},
    )

    clock["now"] = datetime.fromisoformat(
        "2026-10-17T21:00:00-03:00"
    )
    _, daytime = post(fresh(night_payload, 150_000, clock))

    assert daytime["status"] == "approved"
    assert check(daytime)["applicable"] is False

    clock["now"] = datetime.fromisoformat(
        "2026-10-17T22:00:00-03:00"
    )
    _, exact = post(fresh(night_payload, 250_000, clock))

    assert exact["status"] == "approved"
    assert check(exact)["consumed_cents"] == 0
    assert check(exact)["limit_cents"] == 250_000

    _, exceeded = post(fresh(night_payload, 1, clock))

    assert exceeded["status"] == "rejected"
    assert check(exceeded)["consumed_cents"] == 250_000