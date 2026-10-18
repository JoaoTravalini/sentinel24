import json

import pytest

from rules.device import evaluate_device


def make_context(total, familiar=None):
    context = {
        "activity": {
            "device_received": {
                "counterparties_1h": total,
            },
        },
    }

    if familiar is not None:
        context["device_profile"] = {
            "assessed": True,
            "familiar_payers_1h": familiar,
        }

    return context


@pytest.mark.parametrize("total,triggered", [
    (0, False),
    (2, False),
    (3, True),
])
def test_without_established_history_keeps_initial_policy(
    total, triggered
):
    result = evaluate_device(make_context(total))

    assert bool(result["signals"]) is triggered
    assert result["summary"]["unfamiliar_payers_1h"] == total


def test_established_sharing_is_not_unusual_alone():
    result = evaluate_device(
        make_context(total=3, familiar=3)
    )

    assert result["signals"] == []
    assert result["summary"]["unfamiliar_payers_1h"] == 0


@pytest.mark.parametrize("total,familiar,triggered", [
    (4, 3, False),
    (5, 3, False),
    (6, 3, True),
    (10, 0, True),
])
def test_counts_unfamiliar_relationships_instead_of_all_users(
    total, familiar, triggered
):
    result = evaluate_device(
        make_context(total, familiar)
    )

    assert bool(result["signals"]) is triggered
    assert (
        result["summary"]["unfamiliar_payers_1h"]
        == total - familiar
    )


def test_known_users_do_not_hide_new_users():
    result = evaluate_device(
        make_context(total=103, familiar=100)
    )

    assert result["signals"][0]["code"] == "SHARED_DEVICE"
    assert (
        result["signals"][0]["evidence"]["unfamiliar_payers_1h"]
        == 3
    )


def test_unassessed_history_does_not_prove_familiarity():
    context = make_context(3)
    context["device_profile"] = {
        "assessed": False,
        "familiar_payers_1h": 3,
    }

    result = evaluate_device(context)

    assert result["signals"][0]["code"] == "SHARED_DEVICE"
    assert result["summary"]["familiar_payers_1h"] == 0


def test_explanation_contains_counts_without_individual_documents():
    result = evaluate_device(make_context(6, 3))
    serialized = json.dumps(result)

    assert "source_document" not in serialized
    assert (
        result["summary"]["reference"]
        == "established_relationships"
    )