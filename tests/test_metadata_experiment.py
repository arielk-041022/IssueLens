"""Synthetic in-memory metadata only. No real issues, GT, or scorer execution."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch


_SPEC = importlib.util.spec_from_file_location(
    'metadata_experiment', Path(__file__).resolve().parents[1] / 'src/metadata_experiment.py')
M = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(M)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def fixture(size=12):
    """Hashes describe synthetic reference DATA, never authenticated source facts."""
    blocked = 'src/main/java/fiction/Blocked.java'
    catalog = []
    for i in range(size):
        if i < 2:
            owner = 'fiction.Outer.Inner'
            path = 'src/main/java/fiction/Outer.java'
        elif i % 3 == 0:
            owner = 'fiction.Blocked'
            path = blocked
        else:
            owner = 'fiction.Type' + str(i)
            path = 'src/main/java/fiction/Type' + str(i) + '.java'
        signature = 'inspect(' + ('String' if i == 1 else 'int' if i < 3 else 'Arg' + str(i)) + ')'
        catalog.append({'repo_id': 'fiction/metadata', 'source_snapshot_id': '', 'file_path': path,
                        'method_id': 'java-source-v1:' + owner + '#' + signature,
                        'ordinal': i, 'has_body': i % 2 == 0, 'identity_status': 'AVAILABLE',
                        'location_status': 'UNAVAILABLE_MAPPING' if path == blocked else 'AVAILABLE',
                        'features': {'name': 'inspect', 'class': owner.split('.')[-1], 'signature': signature,
                                     'file': path, 'annotations': ['@Synthetic'],
                                     'strings': ['重复\r\n😀', 'same', 'same', ''], 'calls': ['read', 'read']}})
    # The manifest can contain production files with no indexed methods.
    paths = sorted({r['file_path'] for r in catalog} | {blocked})
    manifest = [{'file_path': p, 'source_sha256': hashlib.sha256(('synthetic:' + p).encode()).hexdigest()}
                for p in paths]
    sid = 'sha256:' + digest(manifest)
    for row in catalog:
        row['source_snapshot_id'] = sid
    case = {'case_id': 'synthetic-metadata-case', 'repo_id': 'fiction/metadata', 'source_snapshot_id': sid,
            'issue_text': '  原始描述\r\nSynthetic reproduction 😀\n', 'input_version_status': 'PRE_FIX_UNVERIFIED'}
    bindings = {'schema_version': 'metadata-bindings-v1', 'protocol_sha256': M.PROTOCOL_SHA256,
                'case_metadata_sha256': digest(case), 'catalog_sha256': digest(catalog),
                'catalog_count': len(catalog), 'index_sha256': 'a' * 64, 'metadata_sha256': 'b' * 64,
                'source_manifest': manifest, 'source_manifest_sha256': digest(manifest),
                'source_scope': {'kind': 'CORE_MODULE', 'index_root_relative': 'core',
                                 'production_source_roots': ['src/main/java']},
                'validator_report_sha256': 'c' * 64,
                'validator_summary': {'report_status': 'COMPLETE', 'overall_verdict': 'FAIL', 'exit_code': 1,
                    'index_integrity_status': 'PASS', 'blocking_issues': [{'file_path': blocked,
                        'record_pointer': '/' + str(paths.index(blocked)), 'stage': 'POSITION',
                        'status': 'UNVERIFIABLE', 'reason_code': 'UNSUPPORTED_RAW_MAPPING'}],
                    'summary': {'files_total': len(paths), 'methods_total': len(catalog),
                        'evidence_total': 17, 'verified': 0, 'unverifiable': 17, 'failed': 0, 'blocking_issues': 1}},
                'code_sha256': {p: hashlib.sha256(('synthetic-code:' + p).encode()).hexdigest()
                    for p in ('Main.java', 'evidence_validator.py', 'retriever.py', 'bm25_baseline.py', 'metadata_experiment.py')}}
    return case, catalog, bindings


class MetadataExperimentTests(unittest.TestCase):
    def setUp(self):
        self.case, self.catalog, self.bindings = fixture()

    def build(self):
        return M.build_metadata_request(self.case, self.catalog, self.bindings)

    def rejects(self, code=None):
        with self.assertRaises(M.MetadataRequestError) as cm:
            self.build()
        if code:
            self.assertEqual(cm.exception.reason_code, code)

    def test_normal_build_and_validate(self):
        req = self.build()
        result = M.validate_metadata_request(req, self.bindings)
        self.assertEqual(result['status'], 'VALID')
        self.assertEqual(result['catalog_count'], 12)
        self.assertEqual(req['schema_version'], 'metadata-request-v1')

    def test_entire_original_catalog_preserved(self):
        self.assertEqual(self.build()['catalog'], self.catalog)

    def test_unavailable_mapping_retained(self):
        req = self.build()
        expected = [r for r in self.catalog if r['location_status'] == 'UNAVAILABLE_MAPPING']
        self.assertTrue(expected)
        self.assertEqual([r for r in req['catalog'] if r['location_status'] == 'UNAVAILABLE_MAPPING'], expected)
        report = M.validate_metadata_request(req, self.bindings)
        self.assertEqual(report['location_status_counts']['UNAVAILABLE_MAPPING'], len(expected))

    def test_bodyless_methods_preserved(self):
        self.assertEqual([r['has_body'] for r in self.build()['catalog']], [r['has_body'] for r in self.catalog])

    def test_original_features_order_duplicates_unicode(self):
        self.assertEqual(self.build()['catalog'][0]['features']['strings'], ['重复\r\n😀', 'same', 'same', ''])

    def test_nested_types_and_overloads_not_merged(self):
        ids = [r['method_id'] for r in self.build()['catalog']]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn('java-source-v1:fiction.Outer.Inner#inspect(int)', ids)
        self.assertIn('java-source-v1:fiction.Outer.Inner#inspect(String)', ids)

    def test_same_method_name_different_file(self):
        req = self.build()
        self.assertEqual(req['catalog'][0]['features']['name'], req['catalog'][2]['features']['name'])
        self.assertNotEqual(req['catalog'][0]['file_path'], req['catalog'][2]['file_path'])

    def test_empty_catalog_valid_against_empty_reference(self):
        case, rows, bindings = fixture(0)
        self.assertEqual(M.validate_metadata_request(M.build_metadata_request(case, rows, bindings), bindings)['catalog_count'], 0)

    def test_method_removed_rejected(self):
        self.catalog.pop()
        self.rejects('CATALOG_COUNT_MISMATCH')

    def test_method_added_rejected(self):
        self.catalog.append(copy.deepcopy(self.catalog[-1]))
        self.rejects('CATALOG_COUNT_MISMATCH')

    def test_removing_mapping_unavailable_not_allowed(self):
        self.catalog = [r for r in self.catalog if r['location_status'] != 'UNAVAILABLE_MAPPING']
        self.rejects('CATALOG_COUNT_MISMATCH')

    def test_reordered_catalog_rejected(self):
        self.catalog.reverse()
        self.rejects('ORIGINAL_ORDINAL_MISMATCH')

    def test_reorder_and_renumber_still_rejected(self):
        self.catalog.reverse()
        for i, r in enumerate(self.catalog):
            r['ordinal'] = i
        self.rejects('ORIGINAL_CATALOG_HASH_MISMATCH')

    def test_original_ordinal_not_repaired(self):
        self.catalog[0]['ordinal'] = 100
        self.rejects('ORIGINAL_ORDINAL_MISMATCH')

    def test_boolean_ordinal_rejected(self):
        self.catalog[0]['ordinal'] = False
        self.rejects('ORIGINAL_ORDINAL_MISMATCH')

    def test_identity_unavailable_rejects_whole_case(self):
        for state in ('UNAVAILABLE_PARSE', 'CONFLICT', 'UNSUPPORTED_LOCAL_TYPE', None):
            with self.subTest(state=state):
                self.catalog[0]['identity_status'] = state
                self.rejects('INVALID_CATALOG_IDENTITY_UNAVAILABLE')

    def test_missing_identity_rejected(self):
        del self.catalog[0]['method_id']
        self.rejects('UNKNOWN_OR_MISSING_CATALOG_FIELDS')

    def test_duplicate_identity_rejected(self):
        self.catalog[1] = copy.deepcopy(self.catalog[0])
        self.catalog[1]['ordinal'] = 1
        self.rejects('DUPLICATE_METHOD_IDENTITY')

    def test_duplicate_method_id_across_files_rejected(self):
        self.catalog[2]['method_id'] = self.catalog[0]['method_id']
        self.catalog[2]['features']['class'] = self.catalog[0]['features']['class']
        self.rejects('DUPLICATE_METHOD_IDENTITY')

    def test_legacy_identity_not_upgraded(self):
        self.catalog[0]['method_id'] = 'Inner#inspect'
        self.rejects('INVALID_METHOD_ID_SCHEME')

    def test_method_signature_conflict(self):
        self.catalog[0]['features']['signature'] = 'inspect(float)'
        self.rejects('METHOD_DECLARATION_MISMATCH')

    def test_method_owner_conflict(self):
        self.catalog[0]['features']['class'] = 'Other'
        self.rejects('METHOD_DECLARATION_MISMATCH')

    def test_cross_repo_rejected(self):
        self.catalog[0]['repo_id'] = 'fiction/other'
        self.rejects('CATALOG_REPO_OR_SNAPSHOT_MISMATCH')

    def test_cross_snapshot_rejected(self):
        self.catalog[0]['source_snapshot_id'] = 'sha256:' + '0' * 64
        self.rejects('CATALOG_REPO_OR_SNAPSHOT_MISMATCH')

    def test_paths_not_normalized(self):
        for value in ('../src/main/java/X.java', 'src\\main\\java\\X.java', '/src/main/java/X.java',
                      'src/main/java/../X.java', 'src/test/java/X.java', 'src/main/java/X.java?x=1'):
            with self.subTest(path=value):
                self.catalog[0]['file_path'] = value
                self.rejects('INVALID_CATALOG_FILE')

    def test_feature_file_conflict(self):
        self.catalog[0]['features']['file'] = self.catalog[2]['file_path']
        self.rejects('FEATURE_FILE_MISMATCH')

    def test_feature_array_change_rejected(self):
        self.catalog[0]['features']['calls'].reverse()
        self.catalog[0]['features']['calls'].append('new')
        self.rejects('ORIGINAL_CATALOG_HASH_MISMATCH')

    def test_mapping_status_not_upgraded(self):
        next(r for r in self.catalog if r['location_status'] == 'UNAVAILABLE_MAPPING')['location_status'] = 'AVAILABLE'
        self.rejects('LOCATION_BLOCKER_INCONSISTENCY')

    def test_unknown_mapping_status(self):
        self.catalog[0]['location_status'] = 'UNAVAILABLE_PARSE'
        self.rejects('INVALID_LOCATION_STATUS')

    def test_validator_fail_preserved(self):
        req = self.build()
        self.assertEqual(req['bindings']['validator_summary'], self.bindings['validator_summary'])
        self.assertEqual(req['assurance']['evidence_validation_status'], 'FAIL')
        self.assertEqual(req['assurance']['source_evidence_status'], 'UNVERIFIABLE')

    def test_validator_pass_cannot_replace_failure(self):
        self.bindings['validator_summary'].update(overall_verdict='PASS', exit_code=0)
        self.rejects('UNSUPPORTED_VALIDATOR_STATE')

    def test_aborted_validator_rejected(self):
        self.bindings['validator_summary']['report_status'] = 'ABORTED'
        self.rejects('UNSUPPORTED_VALIDATOR_STATE')

    def test_bad_index_integrity_rejected(self):
        self.bindings['validator_summary']['index_integrity_status'] = 'FAIL'
        self.rejects('UNSUPPORTED_VALIDATOR_STATE')

    def test_other_blocker_not_waived(self):
        self.bindings['validator_summary']['blocking_issues'][0]['reason_code'] = 'SOURCE_HASH_MISMATCH'
        self.rejects('UNSUPPORTED_BLOCKING_ISSUE')

    def test_blocker_unknown_file(self):
        self.bindings['validator_summary']['blocking_issues'][0]['file_path'] = 'src/main/java/fiction/Missing.java'
        self.rejects('UNKNOWN_BLOCKING_FILE')

    def test_blocker_bad_pointer(self):
        for value in ('/01', '/1/methods/2', '/9999999', '0', True, []):
            with self.subTest(pointer=value):
                self.bindings['validator_summary']['blocking_issues'][0]['record_pointer'] = value
                self.rejects('INVALID_FILE_POINTER')

    def test_blocker_count_mismatch(self):
        self.bindings['validator_summary']['summary']['blocking_issues'] = 2
        self.rejects('INVALID_BLOCKING_ISSUES_COUNT')

    def test_duplicate_blockers(self):
        issues = self.bindings['validator_summary']['blocking_issues']
        issues.append(copy.deepcopy(issues[0]))
        self.bindings['validator_summary']['summary']['blocking_issues'] = 2
        self.rejects('DUPLICATE_BLOCKING_FILE_OR_POINTER')

    def test_evidence_cannot_be_verified(self):
        self.bindings['validator_summary']['summary'].update(verified=1, unverifiable=16)
        self.rejects('VALIDATOR_COUNTS_MISMATCH')

    def test_validator_method_count_mismatch(self):
        self.bindings['validator_summary']['summary']['methods_total'] -= 1
        self.rejects('VALIDATOR_COUNTS_MISMATCH')

    def test_case_binding_tamper(self):
        self.case['issue_text'] += 'changed'
        self.rejects('CASE_BINDING_MISMATCH')

    def test_issue_original_whitespace_preserved(self):
        self.assertEqual(self.build()['case_metadata']['issue_text'], self.case['issue_text'])

    def test_issue_text_hash_uses_exact_utf8_bytes(self):
        self.assertEqual(self.build()['issue_text_sha256'],
                         hashlib.sha256(self.case['issue_text'].encode('utf-8')).hexdigest())

    def test_issue_version_not_approved(self):
        for status in ('PRE_FIX_VERIFIED', 'PRE_FIX_UNVERIFIED', 'POST_FIX_MODIFIED'):
            self.case['input_version_status'] = status
            self.bindings['case_metadata_sha256'] = digest(self.case)
            result = M.validate_metadata_request(self.build(), self.bindings)
            self.assertEqual(result['input_version_status'], status)
            self.assertFalse(result['assurance']['execution_authorized'])

    def test_unrecognized_version_rejected(self):
        self.case['input_version_status'] = 'READY'
        self.rejects('INVALID_INPUT_VERSION_STATUS')

    def test_manifest_source_hash_tamper(self):
        self.bindings['source_manifest'][0]['source_sha256'] = '1' * 64
        self.rejects('MANIFEST_SNAPSHOT_MISMATCH')

    def test_manifest_duplicate(self):
        self.bindings['source_manifest'][1] = copy.deepcopy(self.bindings['source_manifest'][0])
        self.rejects('DUPLICATE_MANIFEST_PATH')

    def test_manifest_unsorted_not_repaired(self):
        self.bindings['source_manifest'].reverse()
        self.rejects('UNSORTED_SOURCE_MANIFEST')

    def test_source_scope_preserved(self):
        self.assertEqual(self.build()['bindings']['source_scope'], self.bindings['source_scope'])

    def test_repo_root_scope(self):
        self.bindings['source_scope'].update(kind='REPOSITORY_PRODUCTION', index_root_relative='.')
        self.assertEqual(self.build()['bindings']['source_scope']['index_root_relative'], '.')

    def test_source_scope_not_guessed(self):
        self.bindings['source_scope']['index_root_relative'] = '../core'
        self.rejects('INVALID_SOURCE_SCOPE')

    def test_scan_range_change_rejected(self):
        self.bindings['source_scope']['production_source_roots'].append('src/test/java')
        self.rejects('UNSUPPORTED_PRODUCTION_SOURCE_ROOTS')

    def test_bound_hashes_require_lowercase_sha256(self):
        for key in ('index_sha256', 'metadata_sha256', 'validator_report_sha256', 'catalog_sha256'):
            with self.subTest(key=key):
                case, rows, b = fixture()
                b[key] = 'A' * 64
                with self.assertRaises(M.MetadataRequestError):
                    M.build_metadata_request(case, rows, b)

    def test_code_binding_keys_strict(self):
        self.bindings['code_sha256']['sidecar'] = 'e' * 64
        self.rejects('INVALID_CODE_BINDING_FIELDS')

    def test_protocol_hash_mismatch(self):
        self.bindings['protocol_sha256'] = '0' * 64
        self.rejects('PROTOCOL_HASH_MISMATCH')

    def test_unknown_gt_prediction_fields_all_levels(self):
        for key in ('gt', 'accepted_targets', 'targets', 'patch', 'repair_tests', 'sidecar', 'ranking', 'score', 'hit'):
            for level in ('case', 'record', 'feature', 'bindings', 'blocker'):
                with self.subTest(key=key, level=level):
                    case, rows, b = fixture()
                    obj = {'case': case, 'record': rows[0], 'feature': rows[0]['features'],
                           'bindings': b, 'blocker': b['validator_summary']['blocking_issues'][0]}[level]
                    obj[key] = 'forbidden'
                    with self.assertRaises(M.MetadataRequestError):
                        M.build_metadata_request(case, rows, b)

    def test_unknown_request_field(self):
        req = self.build()
        req['ranking'] = []
        with self.assertRaisesRegex(M.MetadataRequestError, 'UNKNOWN_OR_MISSING_REQUEST_FIELDS'):
            M.validate_metadata_request(req, self.bindings)

    def test_external_reference_not_replaced_by_embedded_bindings(self):
        req = self.build()
        req['bindings']['index_sha256'] = 'd' * 64
        unsigned = {k: v for k, v in req.items() if k != 'request_sha256'}
        req['request_sha256'] = digest(unsigned)
        with self.assertRaisesRegex(M.MetadataRequestError, 'EXTERNAL_BINDINGS_MISMATCH'):
            M.validate_metadata_request(req, self.bindings)

    def test_embedded_binding_types_not_hidden_by_json_equivalence(self):
        req = self.build()
        # Python's JSON encoder treats tuples like lists; the protocol does not.
        req['bindings']['source_manifest'] = tuple(req['bindings']['source_manifest'])
        with self.assertRaisesRegex(M.MetadataRequestError, 'INVALID_SOURCE_MANIFEST'):
            M.validate_metadata_request(req, self.bindings)

    def test_request_hash_tamper(self):
        req = self.build()
        req['request_sha256'] = '0' * 64
        with self.assertRaisesRegex(M.MetadataRequestError, 'REQUEST_CONTENT_OR_HASH_MISMATCH'):
            M.validate_metadata_request(req, self.bindings)

    def test_rehash_does_not_hide_catalog_edit(self):
        req = self.build()
        req['catalog'][0]['features']['strings'].append('changed')
        req['request_sha256'] = digest({k: v for k, v in req.items() if k != 'request_sha256'})
        with self.assertRaisesRegex(M.MetadataRequestError, 'ORIGINAL_CATALOG_HASH_MISMATCH'):
            M.validate_metadata_request(req, self.bindings)

    def test_fake_verified_assurance_rejected(self):
        req = self.build()
        req['assurance']['source_evidence_status'] = 'VERIFIED'
        req['request_sha256'] = digest({k: v for k, v in req.items() if k != 'request_sha256'})
        with self.assertRaises(M.MetadataRequestError):
            M.validate_metadata_request(req, self.bindings)

    def test_request_count_bool_rejected(self):
        req = self.build()
        req['catalog_count'] = True
        with self.assertRaises(M.MetadataRequestError):
            M.validate_metadata_request(req, self.bindings)

    def test_no_authorization_or_root_cause_claim(self):
        assurance = self.build()['assurance']
        self.assertEqual(assurance['binding_assurance'], 'CALLER_SUPPLIED_REFERENCE_ONLY')
        self.assertEqual(assurance['feature_verification_status'], 'UNVERIFIED')
        self.assertEqual(assurance['issue_relevance_status'], 'NOT_ASSESSED')
        for key in ('execution_authorized', 'prediction_performed', 'independent_ast_parse'):
            self.assertIs(assurance[key], False)

    def test_canonical_hash_spec(self):
        req = self.build()
        self.assertEqual(req['request_sha256'], digest({k: v for k, v in req.items() if k != 'request_sha256'}))
        self.assertEqual(M.PROTOCOL_SHA256, digest(M.PROTOCOL))

    def test_key_order_does_not_change_hash(self):
        a = self.build()
        b = M.build_metadata_request(dict(reversed(list(self.case.items()))), self.catalog,
                                     dict(reversed(list(self.bindings.items()))))
        self.assertEqual(a['request_sha256'], b['request_sha256'])

    def test_inputs_unchanged_and_results_detached(self):
        original = copy.deepcopy((self.case, self.catalog, self.bindings))
        req = self.build()
        M.validate_metadata_request(req, self.bindings)
        self.assertEqual((self.case, self.catalog, self.bindings), original)
        req['catalog'][0]['features']['strings'].append('mutation')
        self.assertEqual((self.case, self.catalog, self.bindings), original)

    def test_failure_does_not_modify_inputs(self):
        self.catalog[0]['identity_status'] = 'CONFLICT'
        original = copy.deepcopy((self.case, self.catalog, self.bindings))
        self.rejects()
        self.assertEqual((self.case, self.catalog, self.bindings), original)

    def test_deterministic_json_roundtrip(self):
        req = self.build()
        self.assertEqual(canonical(req), canonical(self.build()))
        self.assertEqual(M.validate_metadata_request(json.loads(canonical(req)), self.bindings)['status'], 'VALID')

    def test_invalid_field_types(self):
        for field, value in [('ordinal', '0'), ('has_body', 1), ('features', []), ('method_id', None),
                             ('identity_status', []), ('location_status', {}), ('file_path', 42)]:
            with self.subTest(field=field):
                case, rows, b = fixture()
                rows[0][field] = value
                with self.assertRaises(M.MetadataRequestError):
                    M.build_metadata_request(case, rows, b)

    def test_nonfinite_or_wrong_feature_types(self):
        for field, value in [('name', float('nan')), ('strings', ['ok', float('inf')]), ('calls', ('read',))]:
            case, rows, b = fixture()
            rows[0]['features'][field] = value
            with self.assertRaises(M.MetadataRequestError):
                M.build_metadata_request(case, rows, b)

    def test_overlong_inputs_rejected_not_truncated(self):
        for level in ('issue', 'feature', 'array'):
            case, rows, b = fixture()
            if level == 'issue':
                case['issue_text'] = 'x' * 100001
            elif level == 'feature':
                rows[0]['features']['strings'] = ['x' * 65537]
            else:
                rows[0]['features']['calls'] = ['x'] * 10001
            with self.assertRaises(M.MetadataRequestError):
                M.build_metadata_request(case, rows, b)

    def test_surrogates_rejected(self):
        self.case['issue_text'] = 'bad\ud800'
        self.rejects('INVALID_ISSUE_TEXT')

    def test_missing_or_bad_top_level_objects(self):
        for args in ((None, self.catalog, self.bindings), (self.case, None, self.bindings),
                     (self.case, self.catalog, None)):
            with self.assertRaises(M.MetadataRequestError):
                M.build_metadata_request(*args)

    def test_error_does_not_echo_issue(self):
        secret = 'synthetic-sensitive-error-sentinel'
        self.case['issue_text'] = secret
        with self.assertRaises(M.MetadataRequestError) as cm:
            self.build()
        self.assertNotIn(secret, str(cm.exception))

    def test_no_io_network_env_process_or_scorer_import(self):
        def deny(*args, **kwargs):
            raise AssertionError('Forbidden external operation')
        import builtins
        original_import = builtins.__import__
        def guarded_import(name, *args, **kwargs):
            if name in {'retriever', 'bm25_baseline', 'experiment_adapter', 'controlled_authorization', 'evidence_validator'}:
                deny()
            return original_import(name, *args, **kwargs)
        with patch('builtins.open', deny), patch('pathlib.Path.open', deny), \
             patch('pathlib.Path.read_bytes', deny), patch('pathlib.Path.write_bytes', deny), \
             patch('socket.socket', deny), patch('socket.create_connection', deny), \
             patch('subprocess.Popen', deny), patch('os.getenv', deny), \
             patch('os._Environ.__getitem__', deny), patch('builtins.__import__', guarded_import):
            self.assertEqual(M.validate_metadata_request(self.build(), self.bindings)['status'], 'VALID')


if __name__ == '__main__':
    unittest.main()
