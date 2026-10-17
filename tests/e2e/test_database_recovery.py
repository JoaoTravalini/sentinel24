from copy import deepcopy
from pathlib import Path
import subprocess
from time import monotonic, sleep
from uuid import uuid4

import requests

from tests.integration.test_pix_analysis import (
    BASE_URL,
    HEADERS,
    analysis_path,
    payload,
)
from tests.integration.test_risk_decisions import (
    isolated_payload,
)


ROOT = Path(__file__).resolve().parents[2]


def compose(*arguments):
    subprocess.run(
        ["docker", "compose", *arguments],
        cwd=ROOT,
        check=True,
        timeout=60,
    )


def send(method, path, **kwargs):
    kwargs.setdefault("headers", HEADERS)
    kwargs.setdefault("timeout", 10)

    return requests.request(
        method,
        BASE_URL + path,
        **kwargs,
    )


def wait_saved_analysis(path):
    deadline = monotonic() + 30
    last = None

    while monotonic() < deadline:
        try:
            response = send(
                "GET",
                path,
                timeout=3,
            )
        except requests.RequestException as error:
            last = type(error).__name__
        else:
            if response.status_code == 200:
                return response.json()

            assert response.status_code == 503, response.text
            last = response.status_code

        sleep(0.25)

    raise AssertionError(
        f"A API não recuperou o acesso ao banco: {last}"
    )


def test_database_outage_preserves_history_and_allows_safe_retry(
    isolated_payload
):
    created = send(
        "POST",
        "/pix/transaction",
        json=isolated_payload,
    )

    assert created.status_code == 201, created.text

    original = created.json()
    original_path = analysis_path(isolated_payload["id"])

    pending = deepcopy(isolated_payload)
    pending["id"] = f"outage-{uuid4().hex}"

    try:
        compose("stop", "db")

        unavailable = send(
            "POST",
            "/pix/transaction",
            json=pending,
        )

        assert unavailable.status_code == 503, unavailable.text
        assert unavailable.json()["code"] == "SNT001003"

        alive = send(
            "GET",
            "/health_check",
            timeout=3,
        )

        assert alive.status_code == 204, alive.text

    finally:
        compose("up", "-d", "--wait", "db")

    assert wait_saved_analysis(original_path) == original

    missing = send(
        "GET",
        analysis_path(pending["id"]),
    )

    assert missing.status_code == 404, missing.text

    recovered = send(
        "POST",
        "/pix/transaction",
        json=pending,
    )

    assert recovered.status_code == 201, recovered.text
    assert recovered.json()["status"] == "approved"

    repeated = send(
        "POST",
        "/pix/transaction",
        json=pending,
    )

    assert repeated.status_code == 200, repeated.text
    assert repeated.json() == recovered.json()

    assert wait_saved_analysis(original_path) == original