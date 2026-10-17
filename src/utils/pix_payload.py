import hashlib
import json
import math
import re
from copy import deepcopy
from datetime import datetime, timezone
from ipaddress import ip_address

from errors import InvalidSchema


def _validate_json_values(value) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _validate_json_values(item)
    elif isinstance(value, str):
        if "\x00" in value:
            raise InvalidSchema("Strings cannot contain a null character.")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise InvalidSchema("Strings must contain valid UTF-8 text.") from None
    elif isinstance(value, float) and not math.isfinite(value):
        raise InvalidSchema("Numbers must be finite.")


def _normalize_timestamp(value: str, field: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("Timezone is required.")
        return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")
    except (ValueError, OverflowError):
        raise InvalidSchema(
            f"{field} must be a valid timestamp with timezone."
        ) from None


def _normalize_document(value: str, field: str) -> str:
    document = re.sub(r"[./-]", "", value.strip()).upper()
    if re.fullmatch(r"(?:[0-9]{11}|[A-Z0-9]{12}[0-9]{2})", document) is None:
        raise InvalidSchema(f"{field} must have CPF or CNPJ format.")
    return document


def prepare_pix_payload(payload: dict) -> dict:
    """Normalize a payload after its structure passes the JSON schema."""
    _validate_json_values(payload)
    normalized = deepcopy(payload)

    if type(normalized["amount"]) is not int:
        raise InvalidSchema("amount must be an integer number of cents.")

    normalized["transaction_date"] = _normalize_timestamp(
        normalized["transaction_date"], "transaction_date"
    )

    for account_name in ("source_account", "destination_account"):
        account = normalized[account_name]
        account["opening_date"] = _normalize_timestamp(
            account["opening_date"], f"{account_name}.opening_date"
        )
        account["owner"]["document_number"] = _normalize_document(
            account["owner"]["document_number"],
            f"{account_name}.owner.document_number",
        )

    source = normalized["source"]
    try:
        address = ip_address(source["ip"])
        if "%" in source["ip"]:
            raise ValueError("Scoped addresses are not supported.")
    except ValueError:
        raise InvalidSchema("source.ip must be an IPv4 or IPv6 address.") from None

    source["ip"] = str(address)
    source["channel"] = source["channel"].strip().lower()

    if source.get("geolocation") is None:
        source.pop("geolocation", None)
    else:
        for name in ("latitude", "longitude"):
            coordinate = float(source["geolocation"][name])
            source["geolocation"][name] = 0.0 if coordinate == 0 else coordinate

    return normalized


def calculate_payload_hash(normalized_payload: dict) -> str:
    canonical = json.dumps(
        normalized_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()