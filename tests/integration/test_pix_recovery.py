from copy import deepcopy
from uuid import uuid4

from database import Context
from repositories.pix_analysis_repository import (
    PixAnalysisRepository,
)
from tests.integration.test_pix_analysis import (
    analysis_path,
    payload,
    send,
)
from tests.integration.test_risk_decisions import (
    isolated_payload,
)
from utils.pix_payload import prepare_pix_payload


def test_lock_timeout_is_temporary_and_same_request_recovers(
    isolated_payload
):
    context = Context()
    session = context.get_or_create_session()
    repository = PixAnalysisRepository(context)

    try:
        repository.configure_transaction()
        repository.lock_analysis_inputs(
            prepare_pix_payload(isolated_payload)
        )

        unavailable = send(
            "POST",
            "/pix/transaction",
            json=isolated_payload,
        )

        assert unavailable.status_code == 503, unavailable.text
        assert unavailable.json()["code"] == "SNT001003"

        missing = send(
            "GET",
            analysis_path(isolated_payload["id"]),
        )

        assert missing.status_code == 404, missing.text

        unrelated = deepcopy(isolated_payload)
        unrelated["id"] = f"unrelated-{uuid4().hex}"

        for account in (
            "source_account",
            "destination_account",
        ):
            unrelated[account]["owner"]["document_number"] = (
                uuid4().hex[:12].upper() + "00"
            )

        unrelated["source"]["device_id"] = (
            f"unrelated-{uuid4().hex}"
        )

        available = send(
            "POST",
            "/pix/transaction",
            json=unrelated,
        )

        assert available.status_code == 201, available.text
        assert available.json()["status"] == "approved"

    finally:
        session.rollback()
        session.close()

    recovered = send(
        "POST",
        "/pix/transaction",
        json=isolated_payload,
    )

    assert recovered.status_code == 201, recovered.text
    assert recovered.json()["status"] == "approved"

    repeated = send(
        "POST",
        "/pix/transaction",
        json=isolated_payload,
    )

    assert repeated.status_code == 200, repeated.text
    assert repeated.json() == recovered.json()

    fetched = send(
        "GET",
        analysis_path(isolated_payload["id"]),
    )

    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == recovered.json()