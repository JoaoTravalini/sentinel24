from copy import deepcopy
from datetime import datetime

import pytest

from rules.night_limit import (
    ACCOUNT_NIGHT_POLICIES,
    assess_night_limit,
    night_limit_scope,
    night_period,
    source_account_key,
)


@pytest.fixture
def item():
    return {
        "transaction_date": "2026-10-17T15:00:00-03:00",
        "source_account": {
            "branch": "0001",
            "account_number": "12345",
            "owner": {"document_number": "12345678909"},
        },
        "destination_account": {
            "owner": {"document_number": "98765432100"},
        },
    }


@pytest.mark.parametrize("clock,start,active", [
    ("19:59:59.999999", 20, False),
    ("20:00:00", 20, True),
    ("23:59:59", 20, True),
    ("00:00:00", 20, True),
    ("05:59:59.999999", 20, True),
    ("06:00:00", 20, False),
    ("21:59:59.999999", 22, False),
    ("22:00:00", 22, True),
])
def test_period_boundaries(clock, start, active):
    stamp = datetime.fromisoformat(
        f"2026-10-18T{clock}-03:00"
    )
    assert (night_period(stamp, start) is not None) is active


def test_midnight_and_timezone_do_not_reset_the_period():
    before = night_period(
        datetime.fromisoformat("2026-10-17T23:30:00-03:00")
    )
    after = night_period(
        datetime.fromisoformat("2026-10-18T03:30:00Z")
    )

    assert before == after
    assert before[0].isoformat() == "2026-10-17T23:00:00+00:00"
    assert before[1].isoformat() == "2026-10-18T09:00:00+00:00"


@pytest.mark.parametrize("amount,prior,exceeded", [
    (100_000, 0, False),
    (100_001, 0, True),
    (1, 99_999, False),
    (2, 99_999, True),
])
def test_limit_is_cumulative_and_exact(item, amount, prior, exceeded):
    stamp = datetime.fromisoformat(
        "2026-10-17T22:00:00-03:00"
    )
    scope = night_limit_scope(item, stamp)
    result = assess_night_limit(scope, amount, prior)

    assert result["exceeded"] is exceeded
    assert result["projected_cents"] == amount + prior
    assert result["remaining_cents"] == 100_000 - prior


@pytest.mark.parametrize(
    "field", ["source_account", "destination_account"]
)
def test_cnpj_does_not_use_the_pf_to_pf_limit(item, field):
    item[field]["owner"]["document_number"] = "12345678000190"
    stamp = datetime.fromisoformat(
        "2026-10-17T22:00:00-03:00"
    )
    result = assess_night_limit(
        night_limit_scope(item, stamp), 200_000, 0
    )

    assert result["applicable"] is False
    assert result["exceeded"] is False


def test_payload_date_cannot_move_server_night_to_day(item):
    stamp = datetime.fromisoformat(
        "2026-10-17T22:00:00-03:00"
    )
    first = night_limit_scope(item, stamp)

    item["transaction_date"] = "2026-10-18T12:00:00-03:00"

    assert night_limit_scope(item, stamp) == first
    assert first["applicable"] is True


def test_account_configuration_does_not_apply_to_another_account(
    item, monkeypatch
):
    monkeypatch.setitem(
        ACCOUNT_NIGHT_POLICIES,
        source_account_key(item),
        {"limit_cents": 250_000, "start_hour": 22},
    )
    stamp = datetime.fromisoformat(
        "2026-10-17T21:00:00-03:00"
    )

    assert night_limit_scope(item, stamp)["applicable"] is False

    other = deepcopy(item)
    other["source_account"]["account_number"] = "54321"

    assert night_limit_scope(other, stamp)["applicable"] is True

    later = datetime.fromisoformat(
        "2026-10-17T22:00:00-03:00"
    )
    assert night_limit_scope(item, later)["limit_cents"] == 250_000


def test_daytime_is_not_subject_to_the_night_limit(item):
    stamp = datetime.fromisoformat(
        "2026-10-17T15:00:00-03:00"
    )
    result = assess_night_limit(
        night_limit_scope(item, stamp), 200_000, 0
    )

    assert result["applicable"] is False
    assert result["exceeded"] is False


def test_naive_server_time_is_rejected():
    with pytest.raises(ValueError):
        night_period(datetime(2026, 10, 17, 22))


@pytest.mark.parametrize("policy", [
    {"start_hour": 19},
    {"limit_cents": -1},
    {"limit_cents": True},
])
def test_invalid_server_configuration_is_rejected(
    item, monkeypatch, policy
):
    monkeypatch.setitem(
        ACCOUNT_NIGHT_POLICIES,
        source_account_key(item),
        policy,
    )

    with pytest.raises(ValueError):
        night_limit_scope(
            item,
            datetime.fromisoformat("2026-10-17T22:00:00-03:00"),
        )