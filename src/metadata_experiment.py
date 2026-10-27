"""Pure metadata-localization-v1 request construction; no prediction or I/O.

Reference bindings must be established independently of the request. Comparing
their hashes is an integrity check, NOT authentication of the caller's claims,
source evidence certification, GT approval, or permission to run an experiment.
All malformed/inconsistent inputs raise MetadataRequestError. No input is fixed,
filtered, reordered, or truncated. Only Python standard-library data are used.
"""
from copy import deepcopy
import hashlib
import json
import re


IDENTITY = ('repo_id', 'source_snapshot_id', 'file_path', 'method_id')
FEATURES = ('name', 'class', 'signature', 'file', 'annotations', 'strings', 'calls')
_CASE = {'case_id', 'repo_id', 'source_snapshot_id', 'issue_text', 'input_version_status'}
_CATALOG = set(IDENTITY) | {'ordinal', 'has_body', 'features', 'identity_status', 'location_status'}
_BINDINGS = {'schema_version', 'protocol_sha256', 'case_metadata_sha256', 'catalog_sha256',
             'catalog_count', 'index_sha256', 'metadata_sha256', 'source_manifest',
             'source_manifest_sha256', 'source_scope', 'validator_report_sha256',
             'validator_summary', 'code_sha256'}
_VALIDATOR = {'report_status', 'overall_verdict', 'exit_code', 'index_integrity_status',
              'blocking_issues', 'summary'}
_COUNTS = {'files_total', 'methods_total', 'evidence_total', 'verified', 'unverifiable',
           'failed', 'blocking_issues'}
_ISSUE = {'file_path', 'record_pointer', 'stage', 'status', 'reason_code'}
_CODE = {'Main.java', 'evidence_validator.py', 'retriever.py', 'bm25_baseline.py', 'metadata_experiment.py'}
_REQUEST = {'schema_version', 'protocol_id', 'protocol_sha256', 'case_metadata',
            'issue_text_sha256', 'catalog', 'catalog_count', 'bindings', 'assurance', 'request_sha256'}

PROTOCOL = {
    'id': 'metadata-localization-v1',
    'request_schema': 'metadata-request-v1',
    'bindings_schema': 'metadata-bindings-v1',
    'catalog_order': 'ORIGINAL_INDEX_ORDER_ZERO_BASED_ORDINAL',
    'catalog_completeness': 'EXACT_COUNT_AND_HASH_AGAINST_EXTERNAL_REFERENCE',
    'identity_policy': 'ALL_AVAILABLE_UNIQUE_FOUR_TUPLES_AND_METHOD_IDS_OR_REJECT_CASE',
    'mapping_policy': 'PRESERVE_AVAILABLE_AND_UNAVAILABLE_MAPPING_NO_FILTERING',
    'validator_policy': 'COMPLETE_FAIL_EXIT1_INTEGRITY_PASS_RAW_MAPPING_BLOCKERS_ONLY',
    'features': list(FEATURES),
    'canonical_encoding': 'UTF8_SORTED_KEYS_COMPACT_ENSURE_ASCII_FALSE_ALLOW_NAN_FALSE',
    'normalization': 'NONE_NO_STRIP_NO_UNICODE_NORMALIZATION_NO_SORTING_INPUT_ARRAYS',
    'limits': {'catalog_records': 100000, 'manifest_files': 100000,
               'issue_characters': 100000, 'identity_characters': 4096,
               'path_characters': 1024, 'feature_characters': 65536,
               'feature_array_items': 10000, 'canonical_bytes': 67108864},
}


class MetadataRequestError(ValueError):
    """Fixed reason_code only; never echoes issue text or an offending value."""

    def __init__(self, reason_code):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _require(condition, reason):
    if not condition:
        raise MetadataRequestError(reason)


def _fields(value, expected, reason):
    _require(type(value) is dict and set(value) == expected, reason)


def _string(value, limit, *, nonempty=False, identifier=False):
    if type(value) is not str or len(value) > limit:
        return False
    if nonempty and not value.strip():
        return False
    if identifier and (not value or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        return False
    try:
        value.encode('utf-8', errors='strict')
    except UnicodeError:
        return False
    return True


def _hash(value):
    return type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _canonical(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                         allow_nan=False).encode('utf-8', errors='strict')
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise MetadataRequestError('NON_CANONICAL_JSON') from None
    _require(len(raw) <= PROTOCOL['limits']['canonical_bytes'], 'CANONICAL_JSON_TOO_LARGE')
    return raw


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


PROTOCOL_SHA256 = _digest(PROTOCOL)


def _path(value, *, java=False):
    if not _string(value, 1024, identifier=True) or any(c in value for c in '\\:<>"|?*%#'):
        return False
    parts = value.split('/')
    if not all(p and p not in {'.', '..'} and not p.endswith(('.', ' ')) for p in parts):
        return False
    return not java or (parts[:3] == ['src', 'main', 'java'] and len(parts) >= 4 and value.endswith('.java'))


def _validate_case(case):
    _fields(case, _CASE, 'UNKNOWN_OR_MISSING_CASE_FIELDS')
    _require(_string(case['case_id'], 128, identifier=True), 'INVALID_CASE_ID')
    _require(_string(case['repo_id'], 256, identifier=True)
             and re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', case['repo_id']) is not None,
             'INVALID_REPO_ID')
    sid = case['source_snapshot_id']
    _require(type(sid) is str and sid.startswith('sha256:') and _hash(sid[7:]), 'INVALID_SNAPSHOT_ID')
    _require(_string(case['issue_text'], 100000, nonempty=True), 'INVALID_ISSUE_TEXT')
    _require(type(case['input_version_status']) is str and case['input_version_status'] in
             {'PRE_FIX_VERIFIED', 'PRE_FIX_UNVERIFIED', 'POST_FIX_MODIFIED'}, 'INVALID_INPUT_VERSION_STATUS')


def _validate_bindings(case, bindings):
    _fields(bindings, _BINDINGS, 'UNKNOWN_OR_MISSING_BINDING_FIELDS')
    _require(bindings['schema_version'] == 'metadata-bindings-v1', 'INVALID_BINDINGS_SCHEMA')
    _require(bindings['protocol_sha256'] == PROTOCOL_SHA256, 'PROTOCOL_HASH_MISMATCH')
    for key in ('case_metadata_sha256', 'catalog_sha256', 'index_sha256', 'metadata_sha256',
                'source_manifest_sha256', 'validator_report_sha256'):
        _require(_hash(bindings[key]), 'INVALID_BINDING_HASH')
    _require(bindings['case_metadata_sha256'] == _digest(case), 'CASE_BINDING_MISMATCH')
    _require(type(bindings['catalog_count']) is int and 0 <= bindings['catalog_count'] <= 100000,
             'INVALID_CATALOG_COUNT')
    scope = bindings['source_scope']
    _fields(scope, {'kind', 'index_root_relative', 'production_source_roots'}, 'INVALID_SOURCE_SCOPE_FIELDS')
    _require(scope['production_source_roots'] == ['src/main/java'], 'UNSUPPORTED_PRODUCTION_SOURCE_ROOTS')
    _require((scope['kind'] == 'REPOSITORY_PRODUCTION' and scope['index_root_relative'] == '.')
             or (scope['kind'] == 'CORE_MODULE' and _path(scope['index_root_relative'])), 'INVALID_SOURCE_SCOPE')
    manifest = bindings['source_manifest']
    _require(type(manifest) is list and 0 < len(manifest) <= 100000, 'INVALID_SOURCE_MANIFEST')
    paths = []
    for entry in manifest:
        _fields(entry, {'file_path', 'source_sha256'}, 'INVALID_MANIFEST_FIELDS')
        _require(_path(entry['file_path'], java=True) and _hash(entry['source_sha256']), 'INVALID_MANIFEST_ENTRY')
        paths.append(entry['file_path'])
    _require(len(paths) == len(set(paths)), 'DUPLICATE_MANIFEST_PATH')
    _require(paths == sorted(paths), 'UNSORTED_SOURCE_MANIFEST')
    path_set = set(paths)
    _require(_digest(manifest) == bindings['source_manifest_sha256']
             and case['source_snapshot_id'] == 'sha256:' + bindings['source_manifest_sha256'], 'MANIFEST_SNAPSHOT_MISMATCH')
    _fields(bindings['code_sha256'], _CODE, 'INVALID_CODE_BINDING_FIELDS')
    _require(all(_hash(v) for v in bindings['code_sha256'].values()), 'INVALID_CODE_HASH')
    summary = bindings['validator_summary']
    _fields(summary, _VALIDATOR, 'INVALID_VALIDATOR_SUMMARY_FIELDS')
    _require(summary['report_status'] == 'COMPLETE' and summary['overall_verdict'] == 'FAIL'
             and type(summary['exit_code']) is int and summary['exit_code'] == 1
             and summary['index_integrity_status'] == 'PASS', 'UNSUPPORTED_VALIDATOR_STATE')
    counts = summary['summary']
    _fields(counts, _COUNTS, 'INVALID_VALIDATOR_COUNTS_FIELDS')
    _require(all(type(n) is int and n >= 0 for n in counts.values()), 'INVALID_VALIDATOR_COUNTS')
    _require(counts['files_total'] == len(manifest) and counts['methods_total'] == bindings['catalog_count']
             and counts['verified'] == counts['failed'] == 0 and counts['unverifiable'] == counts['evidence_total'],
             'VALIDATOR_COUNTS_MISMATCH')
    issues = summary['blocking_issues']
    _require(type(issues) is list and 0 < len(issues) <= len(manifest)
             and counts['blocking_issues'] == len(issues), 'INVALID_BLOCKING_ISSUES_COUNT')
    blocked = set()
    pointers = set()
    for issue in issues:
        _fields(issue, _ISSUE, 'INVALID_BLOCKING_ISSUE_FIELDS')
        pointer = issue['record_pointer']
        _require(type(pointer) is str and re.fullmatch(r'/(0|[1-9][0-9]*)', pointer) is not None
                 and len(pointer) <= 8 and int(pointer[1:]) < len(manifest), 'INVALID_FILE_POINTER')
        _require(_path(issue['file_path'], java=True) and issue['file_path'] in path_set, 'UNKNOWN_BLOCKING_FILE')
        _require(issue['reason_code'] == 'UNSUPPORTED_RAW_MAPPING' and issue['stage'] == 'POSITION'
                 and issue['status'] == 'UNVERIFIABLE', 'UNSUPPORTED_BLOCKING_ISSUE')
        _require(issue['file_path'] not in blocked and pointer not in pointers, 'DUPLICATE_BLOCKING_FILE_OR_POINTER')
        blocked.add(issue['file_path'])
        pointers.add(pointer)
    return path_set, blocked


def _validate_catalog(case, catalog, bindings, files, blocked):
    _require(type(catalog) is list and len(catalog) == bindings['catalog_count'], 'CATALOG_COUNT_MISMATCH')
    identities = set()
    method_ids = set()
    for ordinal, record in enumerate(catalog):
        _fields(record, _CATALOG, 'UNKNOWN_OR_MISSING_CATALOG_FIELDS')
        _require(all(_string(record[k], 4096, identifier=True) for k in IDENTITY), 'INVALID_METHOD_IDENTITY')
        _require((record['repo_id'], record['source_snapshot_id']) == (case['repo_id'], case['source_snapshot_id']),
                 'CATALOG_REPO_OR_SNAPSHOT_MISMATCH')
        _require(_path(record['file_path'], java=True) and record['file_path'] in files, 'INVALID_CATALOG_FILE')
        _require(type(record['ordinal']) is int and record['ordinal'] == ordinal, 'ORIGINAL_ORDINAL_MISMATCH')
        _require(type(record['has_body']) is bool, 'INVALID_HAS_BODY')
        _require(record['identity_status'] == 'AVAILABLE', 'INVALID_CATALOG_IDENTITY_UNAVAILABLE')
        _require(type(record['location_status']) is str and record['location_status'] in
                 {'AVAILABLE', 'UNAVAILABLE_MAPPING'}, 'INVALID_LOCATION_STATUS')
        _require((record['location_status'] == 'UNAVAILABLE_MAPPING') == (record['file_path'] in blocked),
                 'LOCATION_BLOCKER_INCONSISTENCY')
        features = record['features']
        _fields(features, set(FEATURES), 'UNKNOWN_OR_MISSING_FEATURE_FIELDS')
        _require(all(_string(features[k], 65536) for k in FEATURES[:4]), 'INVALID_FEATURE_TEXT')
        _require(features['file'] == record['file_path'], 'FEATURE_FILE_MISMATCH')
        for field in FEATURES[4:]:
            values = features[field]
            _require(type(values) is list and len(values) <= 10000
                     and all(_string(v, 65536) for v in values), 'INVALID_FEATURE_ARRAY')
        mid = record['method_id']
        _require(mid.startswith('java-source-v1:') and mid.count('#') == 1, 'INVALID_METHOD_ID_SCHEME')
        owner, signature = mid[len('java-source-v1:'):].split('#')
        _require(all(owner.split('.')) and owner.split('.')[-1] == features['class']
                 and signature == features['signature'] and bool(features['name'])
                 and signature.startswith(features['name'] + '(') and signature.endswith(')'),
                 'METHOD_DECLARATION_MISMATCH')
        key = tuple(record[k] for k in IDENTITY)
        _require(key not in identities and mid not in method_ids, 'DUPLICATE_METHOD_IDENTITY')
        identities.add(key)
        method_ids.add(mid)
    _require(_digest(catalog) == bindings['catalog_sha256'], 'ORIGINAL_CATALOG_HASH_MISMATCH')


def _assurance():
    return {'binding_assurance': 'CALLER_SUPPLIED_REFERENCE_ONLY',
            'feature_kind': 'INDEX_FEATURES', 'feature_verification_status': 'UNVERIFIED',
            'evidence_validation_status': 'FAIL', 'source_evidence_status': 'UNVERIFIABLE',
            'issue_relevance_status': 'NOT_ASSESSED', 'independent_ast_parse': False,
            'execution_authorized': False, 'prediction_performed': False}


def build_metadata_request(case_metadata, catalog, bindings):
    """Build a detached request from an original ordered catalog and reference hashes.

    Missing/conflicting identities reject the WHOLE case. Mapping-unavailable
    methods and bodyless methods are retained. Invalid input raises rather than
    emitting a partial request. Input-version status is preserved, not approved.
    """
    _validate_case(case_metadata)
    files, blocked = _validate_bindings(case_metadata, bindings)
    _validate_catalog(case_metadata, catalog, bindings, files, blocked)
    request = {'schema_version': 'metadata-request-v1', 'protocol_id': 'metadata-localization-v1',
               'protocol_sha256': PROTOCOL_SHA256, 'case_metadata': deepcopy(case_metadata),
               'issue_text_sha256': hashlib.sha256(case_metadata['issue_text'].encode('utf-8')).hexdigest(),
               'catalog': deepcopy(catalog), 'catalog_count': len(catalog),
               'bindings': deepcopy(bindings), 'assurance': _assurance()}
    request['request_sha256'] = _digest(request)
    return request


def validate_metadata_request(request, bindings):
    """Compare with independently supplied reference bindings; return a small report.

    Never use request['bindings'] as a substitute for that external reference.
    No files, source bytes, validator execution, or authorization are inspected.
    A VALID report means structural/reference consistency only.
    """
    _fields(request, _REQUEST, 'UNKNOWN_OR_MISSING_REQUEST_FIELDS')
    _validate_case(request['case_metadata'])
    _validate_bindings(request['case_metadata'], request['bindings'])
    _require(_canonical(request['bindings']) == _canonical(bindings), 'EXTERNAL_BINDINGS_MISMATCH')
    expected = build_metadata_request(request['case_metadata'], request['catalog'], bindings)
    _require(_canonical(request) == _canonical(expected), 'REQUEST_CONTENT_OR_HASH_MISMATCH')
    counts = {s: sum(r['location_status'] == s for r in request['catalog'])
              for s in ('AVAILABLE', 'UNAVAILABLE_MAPPING')}
    return {'schema_version': 'metadata-request-validation-v1', 'status': 'VALID',
            'request_sha256': expected['request_sha256'], 'catalog_count': len(request['catalog']),
            'location_status_counts': counts, 'input_version_status': request['case_metadata']['input_version_status'],
            'assurance': _assurance()}
