"""
SHA-256 hash chain utilities for position_audit_log.
"""
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def _default_serializer(obj: Any) -> str:
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def compute_audit_hash(record: dict) -> str:
    """Compute SHA-256 hash of an audit log record."""
    canonical = json.dumps(record, sort_keys=True, default=_default_serializer)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_chain_hash(prev_hash: str | None, current_record: dict) -> str:
    """Compute hash for hash-chain: SHA-256(prev_hash + current_record_hash)."""
    current_hash = compute_audit_hash(current_record)
    combined = (prev_hash or "") + current_hash
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
