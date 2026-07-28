"""Small strict-validation helpers shared by Module Drill contracts."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable, Mapping

_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ContractError(ValueError):
    """A persisted Module Drill contract is malformed or internally inconsistent."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def exact_object(value: Any, fields: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ContractError(f"{label} must contain exactly {sorted(fields)}")
    return value


def text(value: Any, label: str, *, multiline: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} must be a non-empty string")
    if not multiline and ("\n" in value or "\r" in value):
        raise ContractError(f"{label} must be one line")
    return value


def slug(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SLUG.fullmatch(value):
        raise ContractError(f"{label} must be a stable kebab-case slug")
    return value


def sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ContractError(f"{label} must be a lowercase SHA-256 digest")
    return value


def enum(value: Any, allowed: Iterable[str], label: str) -> str:
    allowed_set = set(allowed)
    if value not in allowed_set:
        raise ContractError(f"{label} must be one of {sorted(allowed_set)}")
    return str(value)


def string_list(value: Any, label: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ContractError(f"{label} must be a list of non-empty strings")
    if not allow_empty and not value:
        raise ContractError(f"{label} must not be empty")
    if len(value) != len(set(value)):
        raise ContractError(f"{label} must not contain duplicates")
    return tuple(value)


def ref_list(value: Any, label: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    """Evidence refs stay opaque here; their resolver owns grammar and I/O."""
    return string_list(value, label, allow_empty=allow_empty)


def mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    return value


def unique_ids(values: Iterable[str], label: str) -> None:
    rows = list(values)
    if len(rows) != len(set(rows)):
        raise ContractError(f"{label} contains duplicate IDs")
