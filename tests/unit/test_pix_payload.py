from copy import deepcopy

import pytest

from errors import InvalidSchema
from utils.pix_payload import calculate_payload_hash, prepare_pix_payload
from utils.schema_handler import SchemaHandler


@SchemaHandler.validate("post_pix_transaction.json")
def prepare(*, payload: dict) -> dict:
    return prepare_pix_payload(payload)


@pytest.fixture
def pix_payload():
    return {
        "id": "pix-000123",
        "amount": 150000,
        "transaction_date": "2026-10-17T22:15:00-03:00",
        "source_account": {
            "branch": "0001",
            "account_number": "88213",
            "opening_date": "2025-03-02T10:00:00-03:00",
            "owner": {"document_number": "123.456.789-09"},
        },
        "destination_account": {
            "participant": "60701190",
            "branch": "3675",
            "account_number": "10442",
            "opening_date": "2026-10-10T09:00:00-03:00",
            "owner": {"document_number": "987.654.321-00"},
        },
        "source": {
            "device_id": "dvc-9f2c71a8",
            "channel": "app",
            "ip": "198.51.100.23",
            "geolocation": {"latitude": -23.56, "longitude": -46.65},
        },
    }


def test_official_example_is_accepted_without_changing_original(pix_payload):
    original = deepcopy(pix_payload)
    normalized = prepare(payload=pix_payload)

    assert pix_payload == original
    assert normalized["id"] == "pix-000123"
    assert normalized["amount"] == 150000
    assert normalized["transaction_date"] == "2026-10-18T01:15:00.000000+00:00"
    assert normalized["source_account"]["branch"] == "0001"
    assert (
        normalized["destination_account"]["owner"]["document_number"]
        == "98765432100"
    )


def test_equivalent_representations_have_the_same_hash(pix_payload):
    equivalent = dict(reversed(list(deepcopy(pix_payload).items())))
    equivalent["transaction_date"] = "2026-10-18T01:15:00Z"
    equivalent["source_account"]["owner"]["document_number"] = "12345678909"
    equivalent["source"]["channel"] = "APP"

    assert calculate_payload_hash(prepare(payload=pix_payload)) == (
        calculate_payload_hash(prepare(payload=equivalent))
    )


def test_one_cent_difference_changes_the_hash(pix_payload):
    changed = deepcopy(pix_payload)
    changed["amount"] += 1

    assert calculate_payload_hash(prepare(payload=pix_payload)) != (
        calculate_payload_hash(prepare(payload=changed))
    )


def test_absent_and_null_geolocation_are_equivalent(pix_payload):
    pix_payload["source"].pop("geolocation")
    missing = prepare(payload=pix_payload)

    pix_payload["source"]["geolocation"] = None
    explicit_null = prepare(payload=pix_payload)

    assert "geolocation" not in explicit_null["source"]
    assert calculate_payload_hash(missing) == calculate_payload_hash(explicit_null)


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ("12.345.678/0001-90", "12345678000190"),
        ("12.ABC.345/01DE-35", "12ABC34501DE35"),
    ],
)
def test_accepts_cnpj_format(pix_payload, document, expected):
    pix_payload["destination_account"]["owner"]["document_number"] = document
    normalized = prepare(payload=pix_payload)

    assert (
        normalized["destination_account"]["owner"]["document_number"]
        == expected
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("amount",), 0),
        (("amount",), -1),
        (("amount",), True),
        (("amount",), "150000"),
        (("amount",), 150000.0),
        (("amount",), 9223372036854775808),
        (("transaction_date",), "2026-10-17T22:15:00"),
        (("transaction_date",), "2026-02-30T22:15:00-03:00"),
        (("source", "ip"), "999.1.1.1"),
        (("source", "ip"), "198.51.100.0/24"),
        (("source", "ip"), "fe80::1%eth0"),
        (("source", "geolocation", "latitude"), 91),
        (("source", "geolocation", "longitude"), -181),
        (("source", "geolocation", "latitude"), float("nan")),
        (("source_account", "owner", "document_number"), "invalid-document"),
        (("id",), "   "),
        (("id",), "pix\x00invalid"),
        (("id",), "pix\ud800invalid"),
    ],
)
def test_rejects_invalid_input(pix_payload, path, value):
    target = pix_payload
    for name in path[:-1]:
        target = target[name]
    target[path[-1]] = value

    with pytest.raises(InvalidSchema):
        prepare(payload=pix_payload)


def test_rejects_unknown_fields(pix_payload):
    pix_payload["ammount"] = 100

    with pytest.raises(InvalidSchema):
        prepare(payload=pix_payload)