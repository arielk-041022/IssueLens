"""Explicit schema3/java-source-v2 binding. Source positions are NOT evidence PASS.

No scoring, GT or network. The Java producer remains the AST/token authority;
Python independently checks exact source hashes, UTF16 ranges and slice hashes.
Reference hashes must be pinned by the caller, not learned from modified input.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

try:
    from . import metadata_experiment as legacy
    from .evidence_validator import SourceLines, unicode_literal_mapping_safe, _read_source, jackson_string
except ImportError:
    import metadata_experiment as legacy
    from evidence_validator import SourceLines, unicode_literal_mapping_safe, _read_source, jackson_string

VERSION = 'metadata-localization-v2'
PROTOCOL = {'id': VERSION, 'index_schema': '3.0', 'method_id_scheme': 'java-source-v2',
            'identity_policy': 'ALL_AVAILABLE_UNIQUE_OR_REJECT_CASE',
            'position_policy': 'PER_METHOD_RAW_RANGE_SLICE_HASH_FILE_EVIDENCE_SEPARATE',
            'features': list(legacy.FEATURES), 'catalog_order': 'ORIGINAL_INDEX_ORDER'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8', errors='strict')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


PROTOCOL_SHA256 = digest(PROTOCOL)


def require(ok, code):
    if not ok:
        raise legacy.MetadataRequestError(code)


def identity_ok(row):
    mid, erased = row.get('method_id'), row.get('identity_signature')
    features = row.get('features', {})
    require(type(features) is dict and set(features) == set(legacy.FEATURES), 'V2_FEATURE_FIELDS')
    require(all(type(features[k]) is str for k in legacy.FEATURES[:4]) and bool(features['name']), 'V2_FEATURE_TYPES')
    require(all(type(features[k]) is list and all(type(v) is str for v in features[k]) for k in legacy.FEATURES[4:]), 'V2_FEATURE_TYPES')
    require(features['file'] == row.get('file_path'), 'V2_FEATURE_PATH')
    require(row.get('identity_status', 'AVAILABLE') == 'AVAILABLE', 'V2_IDENTITY_UNAVAILABLE')
    require(type(mid) is str and mid.startswith('java-source-v2:') and mid.count('#') == 1, 'V2_IDENTITY_SCHEME')
    owner, signature = mid[len('java-source-v2:'):].split('#')
    require(type(erased) is str and signature == erased and owner.split('.')[-1] == features.get('class')
            and erased.startswith(features.get('name', '') + '(') and erased.endswith(')'), 'V2_IDENTITY_DESCRIPTOR')
    require(type(features.get('signature')) is str and features['signature'].startswith(features['name'] + '(')
            and features['signature'].endswith(')'), 'V2_DISPLAY_SIGNATURE')


def opaque_source_safe(text):
    """Conservative lexical gate matching the producer's opaque escape subset.
    No Unicode translation, token-boundary repair or offset adjustment occurs.
    """
    if text.startswith('\ufeff') or '"""' in text:
        return False
    i = 0
    while i < len(text):
        quote = None
        if text.startswith('//', i):
            end = i + 2
            while end < len(text) and text[end] not in '\r\n': end += 1
            begin = i + 2
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            if end < 0: return False
            begin = i + 2
        elif text[i] in "\"'":
            quote = text[i]; begin = i + 1; end = begin
            while end < len(text):
                if text[end] in '\r\n': return False
                if text[end] == '\\': end += 2
                elif text[end] == quote: break
                else: end += 1
            if end >= len(text): return False
        else:
            if text.startswith('\\u', i): return False
            i += 1; continue
        token = text[begin:end]
        for match in re.finditer(r'\\u', token):
            at = match.start()
            if at and token[at-1] == '\\': return False
            hex_text = token[at+2:at+6]
            if len(hex_text) != 4 or not re.fullmatch('[0-9a-fA-F]{4}', hex_text): return False
            decoded = chr(int(hex_text, 16))
            if decoded in '\r\n\\' or (decoded in '*/' if quote is None else decoded == quote): return False
        i = end + (1 if quote else 2 if text.startswith('/*', i) else 0)
    return True


def evidence_id(file, source_hash, mid, kind, coords):
    # Jackson's escaping is part of the Java/Python byte contract.
    values = ['index-schema-3.0', file, source_hash, mid, kind, *coords]
    wire = ('[ ' + ', '.join(str(v) if type(v) is int else jackson_string(v) for v in values) + ' ]').encode('utf-8')
    return 'sha256:' + hashlib.sha256(wire).hexdigest()


def build_metadata_request(case, index_raw, metadata_raw, root, *, index_sha256, metadata_sha256):
    """Verify actual pinned index and sources, retain every method or reject case.
    All result hashes are structural bindings, not externally signed provenance.
    """
    legacy._validate_case(case)
    require(hashlib.sha256(index_raw).hexdigest() == index_sha256 and
            hashlib.sha256(metadata_raw).hexdigest() == metadata_sha256, 'V2_PINNED_HASH_MISMATCH')
    index, meta = json.loads(index_raw), json.loads(metadata_raw)
    require(meta.get('schema_version') == '3.0' and meta.get('method_id_scheme') == 'java-source-v2'
            and meta.get('index_sha256') == index_sha256 and meta.get('parser_version') == '3.26.4'
            and meta.get('position_convention') == '1-based-inclusive-UTF16-tab1'
            and meta.get('preprocess_unicode_escapes') is False and meta.get('source_encoding') == 'UTF-8'
            and meta.get('source_roots') == ['src/main/java'] and type(meta.get('identity_resolution_runtime')) is str,
            'V2_INDEX_VERSION')
    manifest = sorted([{'file_path': f['file'], 'source_sha256': f['source_sha256']} for f in index], key=lambda f:f['file_path'])
    require(len({f['file_path'] for f in manifest}) == len(manifest), 'V2_DUPLICATE_FILE')
    require(case['source_snapshot_id'] == 'sha256:' + digest(manifest), 'V2_SNAPSHOT_MISMATCH')
    catalog, proofs, sources, issues = [], [], [], []
    identities = set()
    for fi, f in enumerate(index):
        require(legacy._path(f['file'], java=True), 'V2_SOURCE_PATH')
        data = _read_source(Path(root).resolve(), f, '/' + str(fi))
        require(hashlib.sha256(data).hexdigest() == f['source_sha256'], 'V2_SOURCE_HASH')
        text = data.decode('utf-8', errors='strict')
        source_lines = SourceLines(text)
        require(f['parse_status'] == 'PARSED' and not f['parse_problems'], 'V2_PARSE_UNAVAILABLE')
        file_gate = not text.startswith('\ufeff') and unicode_literal_mapping_safe(text)
        opaque_gate = opaque_source_safe(text)
        if not file_gate or any(x in f['limitations'] for x in ('SOURCE_TOKEN_MAPPING_UNAVAILABLE', 'BOM_RAW_MAPPING_UNSUPPORTED', 'UNICODE_ESCAPE_RAW_MAPPING_UNSUPPORTED')):
            issues.append({'file_path': f['file'], 'stage': 'POSITION', 'status': 'UNVERIFIABLE',
                           'reason_code': 'UNSUPPORTED_RAW_MAPPING' if not file_gate else 'INDEX_MAPPING_UNAVAILABLE',
                           'record_pointer': '/' + str(fi)})
        for ci, cls in enumerate(f['classes']):
            for mi, method in enumerate(cls['methods']):
                r = {k: case[k] for k in ('repo_id', 'source_snapshot_id')}
                r.update(file_path=f['file'], method_id=method['method_id'], identity_signature=method.get('identity_signature'),
                         ordinal=len(catalog), has_body=method['has_body'], identity_status=method['identity_status'],
                         location_status=method['location_status'], features={
                             **{k:method[k] for k in ('name', 'signature', 'annotations', 'strings', 'calls')},
                             'class': cls['name'], 'file': f['file']})
                identity_ok(r)
                require(method['qualified_class_name'] == r['method_id'][len('java-source-v2:'):].split('#')[0], 'V2_OWNER_MISMATCH')
                require(r['method_id'] not in identities, 'V2_DUPLICATE_IDENTITY'); identities.add(r['method_id'])
                require(method['location_status'] in ('AVAILABLE', 'UNAVAILABLE_MAPPING'), 'V2_LOCATION_STATUS')
                snippet = None
                if method['location_status'] == 'AVAILABLE':
                    require(file_gate or opaque_gate, 'V2_UNSAFE_FILE_MAPPING')
                    try: snippet = source_lines.extract(method)
                    except (ValueError, TypeError, KeyError): raise legacy.MetadataRequestError('V2_INVALID_RANGE') from None
                    require(file_gate or '\\u' not in snippet, 'V2_UNSAFE_METHOD_ESCAPE')
                    require(hashlib.sha256(snippet.encode('utf-8')).hexdigest() == method.get('source_slice_sha256'), 'V2_SLICE_HASH')
                    require(bool(snippet.strip()) and snippet.rstrip().endswith('}' if method['has_body'] else ';'), 'V2_METHOD_BOUNDARY')
                else:
                    require(all(method.get(k) is None for k in ('start_line','start_column','end_line','end_column'))
                            and method.get('source_slice_sha256') is None, 'V2_UNAVAILABLE_HAS_RANGE')
                for e in method['evidence_occurrences']:
                    require(snippet is not None and e['file_path'] == f['file'], 'V2_EVIDENCE_LOCATION')
                    coords = [e[k] for k in ('start_line','start_column','end_line','end_column')]
                    require((method['start_line'],method['start_column']) <= tuple(coords[:2]) <= tuple(coords[2:]) <=
                            (method['end_line'],method['end_column']), 'V2_EVIDENCE_CONTAINMENT')
                    require(source_lines.extract(e) == e['source_snippet'], 'V2_EVIDENCE_SLICE')
                    require(e['evidence_id'] == evidence_id(f['file'], f['source_sha256'], method['method_id'], e['evidence_type'], coords), 'V2_EVIDENCE_ID')
                pointer = f'/{fi}/classes/{ci}/methods/{mi}'
                proofs.append({'ordinal':r['ordinal'], 'pointer':pointer, 'location_status':method['location_status'],
                               'source_slice_sha256':method.get('source_slice_sha256')})
                sources.append({'method_id':r['method_id'], 'ordinal':r['ordinal'], 'source_status':'EXTRACTED' if snippet is not None else 'UNAVAILABLE',
                                'source_excerpt':snippet, 'file_path':f['file']})
                catalog.append(r)
    require(len(catalog) == meta['methods_total'] and len(index) == meta['files_total']
            and meta['identity_unavailable_methods'] == meta['identity_conflict_methods'] == meta['identity_conflict_groups'] == 0
            and meta['conflicting_method_ids'] == []
            and meta['location_unavailable_methods'] == sum(r['location_status']!='AVAILABLE' for r in catalog)
            and meta['evidence_occurrences'] == sum(len(m['evidence_occurrences']) for f in index for c in f['classes'] for m in c['methods'])
            and meta['parse_counts'] == {'PARSED':len(index),'PARTIAL':0,'FAILED':0,'READ_ERROR':0}, 'V2_INDEX_COUNTS')
    # No evidence is upgraded to PASS: retain file-level blockers and unverified AST authority.
    report = {'schema_version':'source-binding-v2', 'report_status':'COMPLETE', 'overall_verdict':'FAIL',
              'issues':issues, 'evidence_status':'NOT_INDEPENDENTLY_ATTESTED',
              'reason':'Position checks are not evidence certification; file blockers are retained.'}
    request = {'schema_version':'metadata-request-v2', 'protocol_id':VERSION, 'protocol_sha256':PROTOCOL_SHA256,
        'case_metadata':deepcopy(case), 'catalog':catalog, 'catalog_count':len(catalog),
        'index_sha256':index_sha256, 'metadata_sha256':metadata_sha256, 'source_manifest':manifest,
        'method_position_proofs':proofs, 'source_contexts':sources, 'validator_summary':report,
        'assurance':{'evidence_validation_status':'FAIL', 'prediction_performed':False}}
    request['request_sha256'] = digest(request)
    return request


def validate_metadata_request(request, reference):
    require(request == reference and request.get('schema_version') == 'metadata-request-v2'
            and request.get('protocol_sha256') == PROTOCOL_SHA256, 'V2_EXTERNAL_BINDINGS')
    body = {k:v for k,v in request.items() if k != 'request_sha256'}
    require(digest(body) == request['request_sha256'], 'V2_REQUEST_HASH')
    require(request['validator_summary']['overall_verdict'] == 'FAIL', 'V2_EVIDENCE_UPGRADE')
    require(len(request['catalog']) == request['catalog_count'] == len(request['method_position_proofs']) == len(request['source_contexts']), 'V2_COUNT')
    seen = set()
    for i, (r, proof, source) in enumerate(zip(request['catalog'], request['method_position_proofs'], request['source_contexts'])):
        identity_ok(r)
        require(r['ordinal'] == proof['ordinal'] == source['ordinal'] == i and source['method_id'] == r['method_id']
                and source['file_path'] == r['file_path'] and proof['location_status'] == r['location_status'], 'V2_METHOD_BINDING')
        require((r['repo_id'],r['source_snapshot_id']) == (request['case_metadata']['repo_id'],request['case_metadata']['source_snapshot_id']), 'V2_SCOPE')
        require(r['method_id'] not in seen, 'V2_DUPLICATE_IDENTITY'); seen.add(r['method_id'])
    return {'status':'VALID', 'request_sha256':request['request_sha256'], 'assurance':request['assurance']}
