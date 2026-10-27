"""Synthetic-only registry tests; no real datasets or predictors."""
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from dataset_registry import validate_registry


def fixture():
    policy = dict(schema_version='dataset-policy-v1', project_scope=['fiction/alpha'],
                  accepted_issue_types=['REGRESSION', 'BUG'],
                  fix_link_standard='DIRECT_PATCH_AND_PARENT_REVIEW',
                  production_source_roots=['src/main/java'], missing_materials='PENDING',
                  target_count=10, stop_rule='FIXED_REGISTRY_NO_RESULT_BASED_REPLACEMENT',
                  related_cases='GROUP_AND_KEEP_TOGETHER')
    rows = [dict(case_id='synthetic-' + str(i), repo_id='fiction/alpha',
                 issue_url='https://github.com/fiction/alpha/issues/' + str(i),
                 issue_text_sha256=('%064x' % i), fix_commit=('%040x' % (10+i)),
                 pre_fix_commit=('%040x' % (20+i)), related_group_id=None,
                 eligibility_status='ELIGIBLE', eligibility_reason='人工材料审核完成',
                 issue_type='BUG', source_available=True, fix_link_review='VERIFIED',
                 java_layout_status='VERIFIED') for i in (1, 2)]
    return rows, policy


class RegistryTests(unittest.TestCase):
    def result(self, change=None):
        rows, policy = fixture()
        if change:
            change(rows, policy)
        return validate_registry(rows, policy)

    def invalid(self, change, code=None):
        r = self.result(change)
        self.assertEqual(r['status'], 'INVALID')
        self.assertIsNone(r['policy_hash'])
        self.assertIsNone(r['registry_hash'])
        if code:
            self.assertIn(code, [e['reason_code'] for e in r['conflicts']])

    def test_valid_counts(self):
        r = self.result()
        self.assertEqual((r['status'], r['record_count'], r['eligible_count'], r['pending_count'], r['excluded_count']),
                         ('VALID', 2, 2, 0, 0))

    def test_empty_registry(self):
        _, p = fixture()
        self.assertEqual(validate_registry([], p)['status'], 'VALID')

    def test_duplicate_case(self):
        self.invalid(lambda r, p: r[1].update(case_id=r[0]['case_id']), 'DUPLICATE_CASE_ID')

    def test_duplicate_url(self):
        self.invalid(lambda r, p: r[1].update(issue_url=r[0]['issue_url']), 'DUPLICATE_ISSUE_URL')

    def test_duplicate_text(self):
        self.invalid(lambda r, p: r[1].update(issue_text_sha256=r[0]['issue_text_sha256']), 'DUPLICATE_ISSUE_TEXT')

    def test_shared_fix_without_group(self):
        self.invalid(lambda r, p: r[1].update(fix_commit=r[0]['fix_commit']), 'SHARED_FIX_NOT_GROUPED')

    def test_shared_fix_group(self):
        rows, p = fixture()
        rows[1].update(fix_commit=rows[0]['fix_commit'], pre_fix_commit=rows[0]['pre_fix_commit'])
        for r in rows:
            r['related_group_id'] = 'synthetic-group'
        result = validate_registry(rows, p)
        self.assertEqual(result['status'], 'VALID')
        self.assertEqual(result['duplicate_groups'], [{'kind': 'SHARED_FIX', 'record_indices': [0, 1]}])

    def test_shared_fix_parent_conflict(self):
        def change(r, p):
            r[1]['fix_commit'] = r[0]['fix_commit']
            for row in r:
                row['related_group_id'] = 'group'
        self.invalid(change, 'SHARED_FIX_PARENT_CONFLICT')

    def test_case_snapshot_conflict(self):
        self.invalid(lambda r, p: r[1].update(case_id=r[0]['case_id'], source_snapshot_id='sha256:'+'a'*64),
                     'CASE_SNAPSHOT_CONFLICT')

    def test_cross_repo_group(self):
        def change(r, p):
            p['project_scope'].append('fiction/beta')
            r[1].update(repo_id='fiction/beta', issue_url='https://github.com/fiction/beta/issues/2')
            for row in r:
                row['related_group_id'] = 'group'
        self.invalid(change, 'GROUP_CROSSES_REPOSITORIES')

    def test_missing_material_pending(self):
        r = self.result(lambda r, p: r[0].update(fix_commit=None, eligibility_status='PENDING'))
        self.assertEqual((r['status'], r['pending_count']), ('VALID', 1))

    def test_missing_material_excluded(self):
        r = self.result(lambda r, p: r[0].update(source_available=False, eligibility_status='EXCLUDED'))
        self.assertEqual((r['status'], r['excluded_count']), ('VALID', 1))

    def test_missing_material_eligible_rejected(self):
        self.invalid(lambda r, p: r[0].pop('fix_link_review'), 'ELIGIBLE_WITHOUT_REQUIRED_MATERIALS')

    def test_exclusion_reason_required(self):
        self.invalid(lambda r, p: r[0].update(eligibility_status='EXCLUDED', eligibility_reason=''))

    def test_missing_policy_exclude(self):
        def change(r, p):
            p['missing_materials'] = 'EXCLUDED'
            r[0].update(source_available=False, eligibility_status='PENDING')
        self.invalid(change, 'MISSING_MATERIAL_STATUS_CONFLICT')

    def test_hashes_bad(self):
        for field in ('issue_text_sha256', 'fix_commit', 'pre_fix_commit', 'source_snapshot_id'):
            with self.subTest(field=field):
                self.invalid(lambda r, p: r[0].update({field: 'not-a-hash'}))

    def test_paths_bad(self):
        for path in ('../src', '/src/main/java', 'C:/src', 'src\\main\\java', 'src/main/java/../X',
                     'src/test/java', 'src/main/java/X.', 'src/main/java/X:stream'):
            with self.subTest(path=path):
                self.invalid(lambda r, p: r[0].update(source_path=path))

    def test_paths_valid(self):
        self.assertEqual(self.result(lambda r, p: r[0].update(source_path='src/main/java/a/X.java'))['status'], 'VALID')

    def test_bad_status(self):
        for status in (True, [], 'READY', 'held-out'):
            self.invalid(lambda r, p: r[0].update(eligibility_status=status))

    def test_unknown_fields(self):
        for field in ('gt', 'accepted_targets', 'ranking', 'score', 'accuracy', 'held_out', 'nested'):
            self.invalid(lambda r, p: r[0].update({field: {'gt': 'secret'}}), 'UNKNOWN_OR_MISSING_RECORD_FIELDS')

    def test_unknown_policy_fields(self):
        self.invalid(lambda r, p: p.update(bm25_threshold=1), 'INVALID_POLICY_FIELDS')

    def test_result_based_policy_rejected(self):
        self.invalid(lambda r, p: p.update(stop_rule='STOP_WHEN_ACCURATE'))

    def test_invalid_container(self):
        _, p = fixture()
        for rows in (None, {}, 'rows', [None]):
            self.assertEqual(validate_registry(rows, p)['status'], 'INVALID')

    def test_unhashable_fields(self):
        for field in ('case_id', 'repo_id', 'issue_url', 'fix_commit', 'related_group_id', 'issue_type', 'source_snapshot_id'):
            with self.subTest(field=field):
                self.invalid(lambda r, p: r[0].update({field: []}))

    def test_invalid_policy_types(self):
        for field in ('project_scope', 'accepted_issue_types', 'production_source_roots', 'target_count', 'missing_materials'):
            self.invalid(lambda r, p: p.update({field: []}))

    def test_target_bool(self):
        self.invalid(lambda r, p: p.update(target_count=True))

    def test_long_fields(self):
        for field in ('case_id', 'eligibility_reason', 'related_group_id', 'issue_url'):
            self.invalid(lambda r, p: r[0].update({field: 'x'*3000}))

    def test_unicode_invalid(self):
        self.invalid(lambda r, p: r[0].update(eligibility_reason='bad\ud800'))

    def test_no_implicit_normalization(self):
        self.invalid(lambda r, p: r[0].update(case_id=' padded '))

    def test_url_exact(self):
        for url in ('http://github.com/fiction/alpha/issues/1', 'https://github.com.evil/fiction/alpha/issues/1',
                    'https://github.com/fiction/alpha/issues/01', 'https://github.com/fiction/alpha/issues/1?x=1'):
            self.invalid(lambda r, p: r[0].update(issue_url=url))

    def test_hash_stable_reordered_records_policy(self):
        rows, p = fixture()
        a = validate_registry(rows, p)
        b = validate_registry(list(reversed(rows)), dict(reversed(list(p.items()))))
        p['accepted_issue_types'].reverse()
        c = validate_registry(rows, p)
        self.assertEqual(a['registry_hash'], b['registry_hash'])
        self.assertEqual(a['policy_hash'], c['policy_hash'])

    def test_hash_matches_spec(self):
        rows, p = fixture()
        result = validate_registry(rows, p)
        p['accepted_issue_types'].sort()
        encode = lambda v: json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
        ph = hashlib.sha256(encode(p)).hexdigest()
        self.assertEqual(result['policy_hash'], ph)
        self.assertEqual(result['registry_hash'], hashlib.sha256(encode(dict(schema_version='dataset-registry-v1',
                         policy_hash=ph, records=rows))).hexdigest())

    def test_content_changes_hash(self):
        self.assertNotEqual(self.result()['registry_hash'], self.result(lambda r, p: r[0].update(eligibility_reason='other'))['registry_hash'])

    def test_input_immutable_deterministic(self):
        r, p = fixture()
        before = copy.deepcopy((r, p))
        self.assertEqual(validate_registry(r, p), validate_registry(r, p))
        self.assertEqual((r, p), before)

    def test_no_io_environment_network_process(self):
        rows, p = fixture()
        def deny(*args, **kwargs):
            raise AssertionError('Forbidden I/O')
        with patch('builtins.open', deny), patch('pathlib.Path.open', deny), patch('socket.socket', deny), \
             patch('os.getenv', deny), patch('os._Environ.__getitem__', deny), patch('subprocess.Popen', deny):
            self.assertEqual(validate_registry(rows, p)['status'], 'VALID')

    def jira_fixture(self):
        rows, p = fixture()
        p['project_scope'] = ['apache/commons-text']
        for i, r in enumerate(rows, 236):
            r.update(repo_id='apache/commons-text',
                     issue_url='https://issues.apache.org/jira/browse/TEXT-' + str(i))
        return rows, p

    def test_jira_official_url(self):
        rows, p = self.jira_fixture()
        self.assertEqual(validate_registry(rows, p)['status'], 'VALID')

    def test_jira_fake_hosts_paths_and_parameters(self):
        urls = ['http://issues.apache.org/jira/browse/TEXT-236',
                'https://issues.apache.org.evil/jira/browse/TEXT-236',
                'https://issues.apache.org@evil/jira/browse/TEXT-236',
                'https://issues.apache.org:443/jira/browse/TEXT-236',
                'https://issues.apache.org/jira/browse/../TEXT-236',
                'https://issues.apache.org/browse/TEXT-236',
                'https://issues.apache.org/jira/browse/TEXT-236/',
                'https://issues.apache.org/jira/browse/TEXT-236?x=1',
                'https://issues.apache.org/jira/browse/TEXT-236#comment',
                'https://issues.apache.org/jira/browse/%54EXT-236']
        for url in urls:
            with self.subTest(url=url):
                rows, p = self.jira_fixture()
                rows[0]['issue_url'] = url
                self.assertIn('INVALID_ISSUE_URL', [e['reason_code'] for e in validate_registry(rows, p)['conflicts']])

    def test_jira_project_number_validation(self):
        for key in ('TEXT-', 'TEXT-0', 'TEXT-0236', 'TEXT--1', 'TEXT-true', 'text-236', 'LANG-236', 'TEXT-236/extra'):
            rows, p = self.jira_fixture()
            rows[0]['issue_url'] = 'https://issues.apache.org/jira/browse/' + key
            self.assertEqual(validate_registry(rows, p)['status'], 'INVALID')

    def test_jira_project_bound_to_repo(self):
        rows, p = self.jira_fixture()
        p['project_scope'].append('fiction/alpha')
        rows[0]['repo_id'] = 'fiction/alpha'
        self.assertIn('INVALID_ISSUE_URL', [e['reason_code'] for e in validate_registry(rows, p)['conflicts']])

    def test_pr_is_not_issue(self):
        for repo in ('fiction/alpha', 'apache/commons-text'):
            rows, p = fixture()
            p['project_scope'].append('apache/commons-text')
            rows[0].update(repo_id=repo, issue_url='https://github.com/' + repo + '/pull/699')
            self.assertEqual(validate_registry(rows, p)['status'], 'INVALID')

    def test_jira_duplicate_still_rejected(self):
        rows, p = self.jira_fixture()
        rows[1]['issue_url'] = rows[0]['issue_url']
        self.assertIn('DUPLICATE_ISSUE_URL', [e['reason_code'] for e in validate_registry(rows, p)['conflicts']])

    def test_jira_gt_fields_still_rejected(self):
        for key in ('gt', 'ranking', 'accuracy', 'score', 'accepted_targets'):
            rows, p = self.jira_fixture()
            rows[0][key] = []
            self.assertEqual(validate_registry(rows, p)['status'], 'INVALID')

    def test_bound_repo_github_url_still_allowed(self):
        rows, p = self.jira_fixture()
        rows[0]['issue_url'] = 'https://github.com/apache/commons-text/issues/236'
        self.assertEqual(validate_registry(rows, p)['status'], 'VALID')

    def test_jira_immutable_stable_hash(self):
        rows, p = self.jira_fixture()
        before = copy.deepcopy((rows, p))
        a = validate_registry(rows, p)
        b = validate_registry(list(reversed(rows)), p)
        self.assertEqual(a['registry_hash'], b['registry_hash'])
        self.assertEqual((rows, p), before)


if __name__ == '__main__':
    unittest.main()
