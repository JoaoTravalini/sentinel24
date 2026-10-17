import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from uuid import UUID, uuid4

import pytest
import requests


BASE_URL = (
    f"http://{os.environ.get('SERVER_LOCALHOST', '127.0.0.1')}:"
    f"{os.environ.get('API_PORT', '3000')}"
)

HEADERS = {
    "INTERNAL-TOKEN": os.environ.get("INTERNAL_TOKEN", "default_token")
}


def send(method, path, **kwargs):
    kwargs.setdefault("headers", HEADERS)

    return requests.request(
        method,
        BASE_URL + path,
        timeout=10,
        **kwargs,
    )


def analysis_path(pix_id):
    return "/pix/transaction/" + quote(pix_id, safe="")


@pytest.fixture
def payload():
    now = datetime.now(timezone.utc)

    return {
        "id": f"pix-{uuid4().hex}",
        "amount": 150000,
        "transaction_date": now.isoformat(),
        "source_account": {
            "branch": "0001",
            "account_number": "88213",
            "opening_date": (now - timedelta(days=365)).isoformat(),
            "owner": {"document_number": "123.456.789-09"},
        },
        "destination_account": {
            "participant": "60701190",
            "branch": "3675",
            "account_number": "10442",
            "opening_date": (now - timedelta(days=100)).isoformat(),
            "owner": {"document_number": "987.654.321-00"},
        },
        "source": {
            "device_id": f"device-{uuid4().hex}",
            "channel": "app",
            "ip": "198.51.100.23",
        },
    }


def test_creates_and_reads_the_saved_analysis(payload):
    created = send("POST", "/pix/transaction", json=payload)

    assert created.status_code == 201, created.text

    body = created.json()

    assert set(body) == {"key", "status", "metadata"}
    UUID(body["key"])
    assert body["status"] in {
        "approved",
        "two_factor_required",
        "rejected",
    }
    assert isinstance(body["metadata"], dict)
    assert body["metadata"]["policy_version"]

    fetched = send("GET", analysis_path(payload["id"]))

    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == body
    assert send("GET", analysis_path(body["key"])).status_code == 404


def test_repeating_a_request_returns_the_same_analysis(payload):
    first = send("POST", "/pix/transaction", json=payload)
    repeated = send("POST", "/pix/transaction", json=payload)

    assert first.status_code == 201, first.text
    assert repeated.status_code == 200, repeated.text
    assert repeated.json() == first.json()


def test_different_ids_represent_different_pix_requests(payload):
    another = deepcopy(payload)
    another["id"] = f"pix-{uuid4().hex}"

    first = send("POST", "/pix/transaction", json=payload)
    second = send("POST", "/pix/transaction", json=another)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["key"] != second.json()["key"]


def test_reusing_id_with_changed_data_conflicts(payload):
    original = send("POST", "/pix/transaction", json=payload)

    assert original.status_code == 201, original.text

    changed = deepcopy(payload)
    changed["amount"] += 1

    conflict = send("POST", "/pix/transaction", json=changed)

    assert conflict.status_code == 409, conflict.text

    fetched = send("GET", analysis_path(payload["id"]))

    assert fetched.json() == original.json()


def test_equivalent_document_masks_do_not_create_another_analysis(payload):
    first = send("POST", "/pix/transaction", json=payload)

    assert first.status_code == 201, first.text

    equivalent = deepcopy(payload)
    equivalent["source_account"]["owner"]["document_number"] = "12345678909"

    repeated = send("POST", "/pix/transaction", json=equivalent)

    assert repeated.status_code == 200, repeated.text
    assert repeated.json() == first.json()


def test_routes_require_internal_token(payload):
    assert send(
        "POST",
        "/pix/transaction",
        json=payload,
        headers={},
    ).status_code == 403

    assert send(
        "GET",
        analysis_path(payload["id"]),
        headers={},
    ).status_code == 403

    assert send(
        "POST",
        "/pix/transaction",
        json=payload,
        headers={"INTERNAL-TOKEN": "incorrect-token"},
    ).status_code == 403

    assert send(
        "GET",
        analysis_path(payload["id"]),
    ).status_code == 404


def test_invalid_input_is_not_saved(payload):
    payload["amount"] = 0

    response = send("POST", "/pix/transaction", json=payload)

    assert response.status_code == 400, response.text
    assert send(
        "GET",
        analysis_path(payload["id"]),
    ).status_code == 404


def test_concurrent_retries_return_one_analysis(payload):
    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(executor.map(
            lambda _: send("POST", "/pix/transaction", json=payload),
            range(8),
        ))

    codes = [response.status_code for response in responses]
    details = [(response.status_code, response.text) for response in responses]

    assert codes.count(201) == 1, details
    assert codes.count(200) == 7, details
    assert len({response.json()["key"] for response in responses}) == 1
    assert all(
        response.json() == responses[0].json()
        for response in responses
    )


def test_pix_id_is_an_opaque_string_and_can_contain_slashes(payload):
    payload["id"] = f"pix/2026/{uuid4().hex}"

    created = send("POST", "/pix/transaction", json=payload)

    assert created.status_code == 201, created.text

    fetched = send("GET", analysis_path(payload["id"]))

    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == created.json()