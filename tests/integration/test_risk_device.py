from datetime import datetime, timedelta, timezone

import pytest

from rules.engine import RiskEngine
from rules.device import evaluate_device
from tests.integration.test_risk_context import NOW, document, history


def seed_relationships(
    add,
    parties,
    *,
    status="approved",
    device_id=None,
    days=(2, 3, 4),
):
    for party in parties:
        for day in days:
            for index in range(4):
                stamp = NOW - timedelta(
                    days=day,
                    seconds=index * 20,
                )

                add(
                    source_document=party,
                    event_at=stamp,
                    received_at=stamp,
                    status=status,
                    device_id=device_id,
                )


def recent_users(add, parties):
    for party in parties:
        add(source_document=party)


def source_and_others(payload):
    return [
        payload["source_account"]["owner"]["document_number"],
        document(),
        document(),
    ]


def test_established_sharing_and_new_payee_are_not_enough_to_require_step_up(
    history
):
    payload, add, load = history
    parties = source_and_others(payload)

    seed_relationships(add, parties)
    recent_users(add, parties)

    payload["destination_account"]["owner"]["document_number"] = (
        document()
    )

    result = RiskEngine().evaluate(payload, load())
    profile = result["metadata"]["context"]["device_profile"]

    assert result["status"] == "approved"
    assert result["metadata"]["risk_score"] == 15
    assert profile["prior_distinct_payers_1h"] == 3
    assert profile["familiar_payers_1h"] == 3
    assert profile["unfamiliar_payers_1h"] == 0
    assert "SHARED_DEVICE" not in result["metadata"]["reason_codes"]


def test_three_new_relationships_still_generate_risk_on_a_known_device(
    history
):
    payload, add, load = history
    parties = source_and_others(payload)

    seed_relationships(add, parties)
    recent_users(
        add,
        parties + [document(), document(), document()],
    )

    payload["destination_account"]["owner"]["document_number"] = (
        document()
    )

    result = RiskEngine().evaluate(payload, load())
    profile = result["metadata"]["context"]["device_profile"]

    assert result["status"] == "two_factor_required"
    assert result["metadata"]["risk_score"] == 45
    assert profile["familiar_payers_1h"] == 3
    assert profile["unfamiliar_payers_1h"] == 3
    assert "SHARED_DEVICE" in result["metadata"]["reason_codes"]


def test_one_new_relationship_does_not_turn_all_known_users_into_risk(
    history
):
    payload, add, load = history
    parties = source_and_others(payload)

    seed_relationships(add, parties)
    recent_users(add, parties + [document()])

    result = evaluate_device(load())

    assert result["signals"] == []
    assert result["summary"]["familiar_payers_1h"] == 3
    assert result["summary"]["unfamiliar_payers_1h"] == 1


def test_two_observed_days_do_not_establish_device_relationships(
    history
):
    payload, add, load = history
    parties = source_and_others(payload)

    seed_relationships(add, parties, days=(2, 3))
    recent_users(add, parties)

    result = evaluate_device(load())

    assert result["summary"]["familiar_payers_1h"] == 0
    assert result["signals"][0]["code"] == "SHARED_DEVICE"


def test_history_of_another_device_does_not_establish_current_relationships(
    history
):
    payload, add, load = history
    parties = source_and_others(payload)

    seed_relationships(
        add,
        parties,
        device_id="another-device",
    )
    recent_users(add, parties)

    assert (
        evaluate_device(load())["summary"]["familiar_payers_1h"]
        == 0
    )


def test_backdated_recent_records_cannot_establish_relationships(
    history
):
    payload, add, load = history
    parties = source_and_others(payload)

    for party in parties:
        for day in (2, 3, 4):
            add(
                source_document=party,
                event_at=NOW - timedelta(days=day),
                received_at=NOW - timedelta(hours=1),
            )

    recent_users(add, parties)
    result = evaluate_device(load())

    assert result["summary"]["familiar_payers_1h"] == 0
    assert result["signals"][0]["code"] == "SHARED_DEVICE"


@pytest.mark.parametrize("status", [
    "rejected",
    "two_factor_required",
])
def test_unapproved_history_does_not_establish_relationships(
    history, status
):
    payload, add, load = history
    parties = source_and_others(payload)

    seed_relationships(add, parties, status=status)
    recent_users(add, parties)

    assert (
        evaluate_device(load())["summary"]["familiar_payers_1h"]
        == 0
    )


def test_three_calendar_days_require_actual_elapsed_time(history):
    payload, add, load = history
    parties = source_and_others(payload)

    stamps = [
        datetime(2026, 10, 13, 23, 59, tzinfo=timezone.utc),
        datetime(2026, 10, 14, 12, tzinfo=timezone.utc),
        datetime(2026, 10, 15, 0, 1, tzinfo=timezone.utc),
    ]

    for party in parties:
        for stamp in stamps:
            add(
                source_document=party,
                event_at=stamp,
                received_at=stamp,
            )

    recent_users(add, parties)

    assert (
        evaluate_device(load())["summary"]["familiar_payers_1h"]
        == 0
    )


def test_expired_relationships_are_not_a_permanent_allowlist(
    history
):
    payload, add, load = history
    parties = source_and_others(payload)

    seed_relationships(add, parties, days=(15, 16, 17))
    recent_users(add, parties)

    assert (
        evaluate_device(load())["summary"]["familiar_payers_1h"]
        == 0
    )