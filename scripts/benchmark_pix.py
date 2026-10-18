import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from math import ceil
import os
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from dotenv import load_dotenv
import requests


load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(
            "Use um inteiro maior que zero."
        )
    return number


def document():
    return uuid4().hex[:12].upper() + "00"


def build_payload(now, payer, destination, device):
    return {
        "id": f"bench-{uuid4().hex}",
        "amount": 10_000,
        "transaction_date": now.isoformat(),
        "source_account": {
            "branch": "0001",
            "account_number": "88213",
            "opening_date": (
                now - timedelta(days=365)
            ).isoformat(),
            "owner": {"document_number": payer},
        },
        "destination_account": {
            "participant": "60701190",
            "branch": "3675",
            "account_number": "10442",
            "opening_date": (
                now - timedelta(days=100)
            ).isoformat(),
            "owner": {"document_number": destination},
        },
        "source": {
            "device_id": device,
            "channel": "app",
            "ip": "198.51.100.23",
        },
    }


def percentile(values, fraction):
    ordered = sorted(values)
    return round(
        ordered[ceil(len(ordered) * fraction) - 1],
        2,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Ensaio local de carga da API Pix."
    )
    parser.add_argument(
        "--requests", type=positive_int, default=200
    )
    parser.add_argument(
        "--concurrency", type=positive_int, default=8
    )
    parser.add_argument(
        "--workload",
        choices=(
            "independent", "same_payer", "shared_device", "same_recipient",
            "accumulated_amount",
        ),
        default="independent",
    )
    parser.add_argument(
        "--budget-ms", type=positive_int, default=2000
    )
    parser.add_argument(
        "--history", type=int, choices=(0, 12, 100), default=0
    )
    parser.add_argument(
        "--vacuum-history",
        action="store_true",
        help="Executa VACUUM ANALYZE antes da medição HTTP.",
    )
    args = parser.parse_args()

    if args.vacuum_history and not args.history:
        parser.error("--vacuum-history exige --history.")

    accumulation = args.workload == "accumulated_amount"
    if accumulation and args.history != 100:
        parser.error("accumulated_amount exige --history 100.")

    base = (
        f"http://{os.environ.get('SERVER_LOCALHOST', '127.0.0.1')}:"
        f"{os.environ.get('API_PORT', '3000')}"
    )
    headers = {
        "INTERNAL-TOKEN": os.environ.get(
            "INTERNAL_TOKEN", "default_token"
        )
    }

    with requests.Session() as session:
        health = session.get(
            base + "/health_check",
            timeout=5,
        )
        health.raise_for_status()

    now = datetime.now(timezone.utc)
    shared_payer = document()
    shared_recipient = document()
    shared_device = f"bench-device-{uuid4().hex}"
    payloads = []

    for _ in range(args.requests):
        same_payer = args.workload == "same_payer"
        same_device = args.workload in (
            "same_payer", "shared_device"
        )

        payloads.append(build_payload(
            now,
            shared_payer if same_payer else document(),
            (
                shared_recipient
                if args.workload == "same_recipient"
                else document()
            ),
            (
                shared_device
                if same_device
                else f"bench-device-{uuid4().hex}"
            ),
        ))

    if accumulation:
        for item in payloads:
            item["amount"] = 90_000

    history_rows = 0

    if args.history:
        from benchmark_history import context_ready, prepare_history

        history_rows = prepare_history(
            payloads, args.history,
            vacuum=args.vacuum_history,
            accumulation=accumulation,
        )

    def worker(items):
        results = []

        with requests.Session() as session:
            session.headers.update(headers)

            for item in items:
                start = perf_counter()
                http_status = "transport_error"
                decision = None
                error = None
                policy_version = None
                device_history_checked = False
                recipient_history_checked = False
                amount_history_checked = False

                try:
                    response = session.post(
                        base + "/pix/transaction",
                        json=item,
                        timeout=5,
                    )
                    http_status = str(response.status_code)

                    if response.status_code != 201:
                        error = f"HTTP_{response.status_code}"
                    else:
                        body = response.json()
                        valid = (
                            isinstance(body, dict)
                            and set(body) == {
                                "key", "status", "metadata"
                            }
                            and body["status"] in (
                                "approved",
                                "two_factor_required",
                                "rejected",
                            )
                            and isinstance(body["metadata"], dict)
                            and body["metadata"].get(
                                "policy_ready"
                            ) is True
                            and isinstance(
                                body["metadata"].get("policy_version"),
                                str,
                            )
                            and (
                                not args.history
                                or context_ready(body["metadata"])
                            )
                        )

                        if valid and accumulation:
                            metadata = body["metadata"]
                            profile = metadata.get("context", {}).get(
                                "amount_profile", {}
                            )
                            amount_history_checked = (
                                profile.get("assessed") is True
                                and profile.get("mature") is True
                            )
                            valid = (
                                amount_history_checked
                                and body["status"] == "two_factor_required"
                                and metadata.get("risk_score") == 35
                                and metadata.get("family_scores")
                                == {"velocity": 35}
                                and set(metadata.get("reason_codes", [])) == {
                                    "ACCUMULATED_AMOUNT_1H",
                                    "ACCUMULATED_AMOUNT_24H",
                                }
                            )

                        if valid:
                            decision = body["status"]
                            policy_version = (
                                body["metadata"]["policy_version"]
                            )
                            device_history_checked = (
                                body["metadata"].get("context", {})
                                .get("device_profile", {})
                                .get("assessed") is True
                            )
                            recipient_history_checked = any(
                                clock.get("assessed") is True
                                for clock in body["metadata"]
                                .get("context", {})
                                .get("recipient_profile", {}).values()
                                if isinstance(clock, dict)
                            )
                        else:
                            error = "invalid_contract"

                except requests.RequestException as exc:
                    error = type(exc).__name__
                except ValueError:
                    error = "invalid_json"

                elapsed = (perf_counter() - start) * 1000

                results.append((
                    elapsed,
                    http_status,
                    decision,
                    error,
                    policy_version,
                    device_history_checked,
                    recipient_history_checked,
                    amount_history_checked,
                ))

        return results

    concurrency = min(args.concurrency, args.requests)
    chunks = [
        payloads[index::concurrency]
        for index in range(concurrency)
    ]

    start = perf_counter()

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        results = [
            row
            for batch in executor.map(worker, chunks)
            for row in batch
        ]

    duration = perf_counter() - start
    latencies = [row[0] for row in results]

    report = {
        "workload": args.workload,
        "history_per_payer": args.history,
        "prepared_history_rows": history_rows,
        "history_statistics_refreshed": bool(args.history),
        "history_vacuumed": bool(
            args.history and args.vacuum_history
        ),
        "requires_mature_profile_and_geo": bool(args.history),
        "device_history_checks": sum(row[5] for row in results),
        "recipient_history_checks": sum(row[6] for row in results),
        "amount_history_checks": sum(row[7] for row in results),
        "requires_mature_amount_profile": accumulation,
        "recent_amount_rows_per_payer": 2 if accumulation else 0,
        "requests": args.requests,
        "concurrency": concurrency,
        "duration_s": round(duration, 2),
        "valid_decisions_rps": round(
            sum(row[2] is not None for row in results) / duration,
            2,
        ),
        "policy_versions": dict(Counter(
            row[4] for row in results if row[4] is not None
        )),
        "http_statuses": dict(Counter(
            row[1] for row in results
        )),
        "decisions": dict(Counter(
            row[2] for row in results if row[2] is not None
        )),
        "errors": dict(Counter(
            row[3] for row in results if row[3] is not None
        )),
        "latency_ms": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
            "max": round(max(latencies), 2),
        },
        "percentile_method": "nearest_rank",
        "budget_ms": args.budget_ms,
        "valid_within_budget": sum(
            1
            for elapsed, _, decision, error, _, _, _, _ in results
            if (
                error is None
                and decision is not None
                and elapsed <= args.budget_ms
            )
        ),
    }

    print(json.dumps(report, indent=2))
    return 1 if any(row[3] is not None for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())