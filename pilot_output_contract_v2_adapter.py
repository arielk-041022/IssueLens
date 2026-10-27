"""Opt-in offline adapter for IssueLens pilot output contract V2.

Does not import, patch, or execute the frozen pilot. No file/network/GT access.
Call only after the original response envelope and budget checks have passed.
"""
from dataclasses import dataclass
from typing import Any

from output_contract_v2 import normalize


@dataclass(frozen=True)
class AdaptedResult:
    status: str
    result: dict | None
    failure_reason: str | None


def adapt_final_v2(obj: Any, catalog: dict, allowed: set[str] | list[str] | tuple[str, ...], *, direct: bool = False) -> AdaptedResult:
    """Normalize ranking and enforce frozen arm-specific candidate eligibility.

    direct=False: path-line locators forbidden; only canonical occurrence IDs
    allowed (consistent with frozen non-direct arm). direct=True: accepts
    supported location syntax if uniquely source-bound. Both require each
    normalized ID in the supplied allowed pool. Does not retry or drop items.
    """
    if not isinstance(obj, dict) or set(obj) != {'ranking', 'reason'}:
        return AdaptedResult('REJECTED', None, 'FINAL_OUTPUT_KEYS')
    reason = obj['reason']
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2048:
        return AdaptedResult('REJECTED', None, 'REASON')
    if not isinstance(allowed, (set, list, tuple)) or not all(isinstance(x, str) for x in allowed):
        return AdaptedResult('REJECTED', None, 'ALLOWED_POOL_INVALID')
    normalized = normalize(obj, catalog)
    if normalized.status != 'ACCEPTED':
        return AdaptedResult('REJECTED', None, normalized.failure_reason)
    if not direct and any(m['mode'] != 'CANONICAL_ID' for m in normalized.mapping):
        return AdaptedResult('REJECTED', None, 'LOCATORS_DIRECT_ONLY')
    allowed_ids = set(allowed)
    if any(x not in allowed_ids for x in normalized.ranking):
        return AdaptedResult('REJECTED', None, 'OUTSIDE_FROZEN_ALLOWED_POOL')
    return AdaptedResult('ACCEPTED', {
        'contract_version': 'output-contract-v2',
        'ranking': list(normalized.ranking),
        'reason': reason,
        'original_ranking': list(obj['ranking']),
        'identity_mapping': list(normalized.mapping),
    }, None)
