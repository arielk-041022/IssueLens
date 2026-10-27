"""IssueLens V2 deterministic, fail-closed output normalization.

Offline library: no file IO, network, GT access, or mutation of inputs.
Not a replacement for the frozen V1 validator or an accuracy scorer.
"""
import re
from dataclasses import dataclass
from typing import Any

CANONICAL = re.compile(r'source-occurrence-compat-v1:sha256:[0-9a-f]{64}\Z')
LOCATION = re.compile(r'(src/main/java/[^:\r\n]+\.java):([1-9][0-9]*):([^\r\n]+)\Z')
METHOD_END = re.compile(r'([A-Za-z_$][\w$]*)\s*\(([^()]*)\)\s*\Z')
PARAM_ANNOT = re.compile(r'@(?:[A-Za-z_$][\w$]*\.)*[A-Za-z_$][\w$]*\s*')
PARAM_MOD = re.compile(r'\b(?:final|volatile)\s+')


@dataclass(frozen=True)
class NormalizedResult:
    status: str
    ranking: tuple[str, ...]
    mapping: tuple[dict, ...]
    failure_reason: str | None = None


def java_parameter_types(declaration: str):
    """Parse deliberately narrow Java signatures. Unsupported syntax fails closed."""
    if not isinstance(declaration, str):
        return None
    m = METHOD_END.search(declaration.strip())
    if not m:
        return None
    name, params = m.groups()
    types = []
    if params.strip():
        for param in params.split(','):
            p = PARAM_ANNOT.sub('', param).strip()
            p = PARAM_MOD.sub('', p).strip()
            parts = p.split()
            if len(parts) != 2 or not re.fullmatch(r'[A-Za-z_$][\w$.]*(?:\[\]|\.\.\.)?', parts[0]):
                return None
            if not re.fullmatch(r'[A-Za-z_$][\w$]*(?:\[\])?', parts[1]):
                return None
            types.append(parts[0].replace('...', '[]'))
    return name, ','.join(types)


def resolve(item: Any, catalog: dict):
    """Return matching records and parsing mode; uniqueness checked by caller."""
    records = catalog.get('records')
    if not isinstance(records, list):
        return [], 'INVALID_CATALOG'
    if not isinstance(item, str):
        return [], 'NON_STRING'
    if CANONICAL.fullmatch(item):
        return [r for r in records if isinstance(r, dict) and r.get('occurrence_id') == item], 'CANONICAL_ID'
    m = LOCATION.fullmatch(item)
    if not m:
        return [], 'UNSUPPORTED_FORMAT'
    file, line, declaration = m.groups()
    parsed = java_parameter_types(declaration)
    if parsed is None:
        return [], 'UNPARSABLE_DECLARATION'
    name, types = parsed
    signature = f'{name}({types})'
    return [r for r in records if isinstance(r, dict)
            and r.get('features', {}).get('file') == file
            and r.get('index_record', {}).get('start_line') == int(line)
            and r.get('features', {}).get('signature') == signature], 'PATH_LINE_SIGNATURE'


def normalize(prediction: Any, catalog: dict) -> NormalizedResult:
    """Normalize a parsed model object. All-or-nothing; preserve original response externally."""
    if not isinstance(prediction, dict):
        return NormalizedResult('REJECTED', (), (), 'NOT_OBJECT')
    if set(prediction) != {'ranking', 'reason'}:
        return NormalizedResult('REJECTED', (), (), 'OUTPUT_FIELDS')
    ranking = prediction['ranking']
    if not isinstance(ranking, list) or not 1 <= len(ranking) <= 5:
        return NormalizedResult('REJECTED', (), (), 'RANKING_LENGTH_OR_TYPE')
    if not isinstance(prediction['reason'], str) or not prediction['reason'].strip():
        return NormalizedResult('REJECTED', (), (), 'REASON_REQUIRED')
    if not isinstance(catalog, dict) or not isinstance(catalog.get('records'), list):
        return NormalizedResult('REJECTED', (), (), 'INVALID_CATALOG')
    out, mapping = [], []
    for i, item in enumerate(ranking):
        found, mode = resolve(item, catalog)
        if len(found) != 1:
            return NormalizedResult('REJECTED', (), (), f'UNRESOLVED_OR_AMBIGUOUS:{i}:{mode}')
        r = found[0]
        if r.get('identity_status') != 'AVAILABLE' or r.get('binding_status') != 'SOURCE_BOUND':
            return NormalizedResult('REJECTED', (), (), f'UNVERIFIABLE_SOURCE:{i}')
        oid = r.get('occurrence_id')
        if not isinstance(oid, str) or not CANONICAL.fullmatch(oid):
            return NormalizedResult('REJECTED', (), (), f'INVALID_CANONICAL_ID:{i}')
        if oid in out:
            return NormalizedResult('REJECTED', (), (), f'DUPLICATE_ID:{i}')
        out.append(oid)
        mapping.append({'rank': i + 1, 'mode': mode, 'raw': item, 'canonical_id': oid})
    return NormalizedResult('ACCEPTED', tuple(out), tuple(mapping))
