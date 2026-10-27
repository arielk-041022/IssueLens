"""In-memory registry-v1 validation; no selection, provenance attestation or I/O.

Canonical encoding: UTF-8 JSON, sorted keys, compact separators, ensure_ascii=False,
allow_nan=False. Records are sorted by case_id; set-like policy lists are sorted.
Text is never stripped or Unicode-normalized. Hashes exist only for VALID reports.
"""
import hashlib
import json
import re
from copy import deepcopy

REQUIRED = {'case_id', 'repo_id', 'issue_url', 'issue_text_sha256', 'fix_commit',
            'pre_fix_commit', 'related_group_id', 'eligibility_status', 'eligibility_reason', 'issue_type'}
OPTIONAL = {'source_available', 'fix_link_review', 'java_layout_status', 'source_snapshot_id', 'source_path'}
POLICY = {'schema_version', 'project_scope', 'accepted_issue_types', 'fix_link_standard',
          'production_source_roots', 'missing_materials', 'target_count', 'stop_rule', 'related_cases'}
STATUSES = {'ELIGIBLE', 'EXCLUDED', 'PENDING'}
# Explicit tracker/project binding; no caller-controlled host or project alias.
JIRA_PROJECTS = {'apache/commons-text': 'TEXT'}


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _text(value, limit=512):
    return (isinstance(value, str) and 0 < len(value) <= limit and value == value.strip()
            and all(ord(c) >= 32 and not 0xD800 <= ord(c) <= 0xDFFF for c in value))


def _hex(value, size):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{%d}' % size, value) is not None


def _path(value):
    return (_text(value) and not any(c in value for c in '\\:?#%<>"|*')
            and all(p and p not in {'.', '..'} and not p.endswith(('.', ' ')) for p in value.split('/')))


def _repo(value):
    return _text(value) and re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+', value) is not None


def _issue_url(value, repo):
    if not _text(value) or not isinstance(repo, str):
        return False
    if re.fullmatch(r'https://github\.com/' + re.escape(repo) + r'/issues/[1-9][0-9]*', value):
        return True
    project = JIRA_PROJECTS.get(repo)
    return (project is not None and re.fullmatch(
        r'https://issues\.apache\.org/jira/browse/' + re.escape(project) + r'-[1-9][0-9]*', value) is not None)


def validate_registry(records, policy):
    """Return VALID/INVALID with diagnostics. Invalid inputs never receive hashes.

    Nullable commit/text-hash fields represent missing material. Such a record
    must remain PENDING or EXCLUDED according to policy. Shared fixes require
    the same non-null related_group_id. Duplicate URLs/text/case IDs are errors.
    Counts describe declared statuses, not automatic approval or verified facts.
    """
    report = dict(schema_version='dataset-registry-report-v1', status='INVALID', record_count=0,
                  eligible_count=0, excluded_count=0, pending_count=0, duplicate_groups=[],
                  conflicts=[], policy_hash=None, registry_hash=None)
    errors = report['conflicts']

    def error(code, indices=()):
        errors.append({'reason_code': code, 'record_indices': list(indices)})

    def check(ok, code, indices=()):
        if not ok:
            error(code, indices)
        return ok

    if not isinstance(policy, dict) or set(policy) != POLICY:
        error('INVALID_POLICY_FIELDS')
        return report
    p = policy
    check(p['schema_version'] == 'dataset-policy-v1', 'INVALID_POLICY_VERSION')
    for key, predicate in [('project_scope', _repo), ('accepted_issue_types', lambda s: s in {'BUG', 'REGRESSION'}),
                           ('production_source_roots', _path)]:
        items = p[key]
        check(isinstance(items, list) and 0 < len(items) <= 1000
              and all(isinstance(s, str) and predicate(s) for s in items)
              and len(set(items)) == len(items), 'INVALID_POLICY_' + key.upper())
    check(p['fix_link_standard'] == 'DIRECT_PATCH_AND_PARENT_REVIEW', 'INVALID_FIX_LINK_STANDARD')
    check(p['missing_materials'] in ('PENDING', 'EXCLUDED'), 'INVALID_MISSING_MATERIALS')
    check(type(p['target_count']) is int and 0 < p['target_count'] <= 10000, 'INVALID_TARGET_COUNT')
    check(p['stop_rule'] == 'FIXED_REGISTRY_NO_RESULT_BASED_REPLACEMENT', 'INVALID_STOP_RULE')
    check(p['related_cases'] == 'GROUP_AND_KEEP_TOGETHER', 'INVALID_RELATED_CASE_POLICY')
    if errors:
        return report
    if not isinstance(records, list) or len(records) > 10000:
        error('INVALID_RECORDS_CONTAINER_OR_LIMIT')
        return report
    report['record_count'] = len(records)
    valid = []
    for i, r in enumerate(records):
        before = len(errors)
        if not isinstance(r, dict) or not REQUIRED <= set(r) or not set(r) <= REQUIRED | OPTIONAL:
            error('UNKNOWN_OR_MISSING_RECORD_FIELDS', [i])
            continue
        for field in ('case_id', 'eligibility_reason'):
            check(_text(r[field], 2048 if field == 'eligibility_reason' else 128), 'INVALID_' + field.upper(), [i])
        check(_repo(r['repo_id']) and r['repo_id'] in p['project_scope'], 'INVALID_OR_OUT_OF_SCOPE_REPO', [i])
        url = r['issue_url']
        check(_issue_url(url, r['repo_id']), 'INVALID_ISSUE_URL', [i])
        check(isinstance(r['issue_type'], str) and r['issue_type'] in p['accepted_issue_types'], 'INVALID_ISSUE_TYPE', [i])
        status = r['eligibility_status']
        check(isinstance(status, str) and status in STATUSES, 'INVALID_STATUS', [i])
        if isinstance(status, str) and status in STATUSES:
            report[status.lower() + '_count'] += 1
        group = r['related_group_id']
        check(group is None or _text(group, 128), 'INVALID_RELATED_GROUP', [i])
        for field, n in [('issue_text_sha256', 64), ('fix_commit', 40), ('pre_fix_commit', 40)]:
            check(r[field] is None or _hex(r[field], n), 'INVALID_' + field.upper(), [i])
        check(r['fix_commit'] is None or r['fix_commit'] != r['pre_fix_commit'], 'IDENTICAL_FIX_AND_PARENT', [i])
        check('source_available' not in r or type(r['source_available']) is bool, 'INVALID_SOURCE_AVAILABLE', [i])
        for field in ('fix_link_review', 'java_layout_status'):
            check(field not in r or (isinstance(r[field], str) and r[field] in {'VERIFIED', 'PENDING', 'FAILED'}), 'INVALID_' + field.upper(), [i])
        if 'source_snapshot_id' in r:
            sid = r['source_snapshot_id']
            check(isinstance(sid, str) and sid.startswith('sha256:') and _hex(sid[7:], 64), 'INVALID_SNAPSHOT', [i])
        if 'source_path' in r:
            check(_path(r['source_path']) and any(r['source_path'] == root or r['source_path'].startswith(root + '/')
                  for root in p['production_source_roots']), 'INVALID_SOURCE_PATH', [i])
        missing = (any(r[f] is None for f in ('issue_text_sha256', 'fix_commit', 'pre_fix_commit'))
                   or r.get('source_available') is not True or r.get('fix_link_review') != 'VERIFIED'
                   or r.get('java_layout_status') != 'VERIFIED')
        if missing:
            check(status == 'EXCLUDED' or status == p['missing_materials'], 'MISSING_MATERIAL_STATUS_CONFLICT', [i])
        if status == 'ELIGIBLE':
            check(not missing, 'ELIGIBLE_WITHOUT_REQUIRED_MATERIALS', [i])
        if len(errors) == before:
            valid.append((i, r))

    # Never silently merge records, including invalid rows with otherwise valid keys.
    for kind, fields in [('CASE_ID', ('case_id',)), ('ISSUE_URL', ('issue_url',)),
                         ('ISSUE_TEXT', ('issue_text_sha256',)), ('SHARED_FIX', ('repo_id', 'fix_commit'))]:
        groups = {}
        for i, r in enumerate(records):
            if isinstance(r, dict) and all(_text(r.get(f), 2048) for f in fields):
                groups.setdefault(tuple(r[f] for f in fields), []).append(i)
        for key, indices in sorted(groups.items()):
            if len(indices) < 2:
                continue
            report['duplicate_groups'].append({'kind': kind, 'record_indices': indices})
            rows = [records[i] for i in indices]
            if kind == 'SHARED_FIX':
                group = rows[0].get('related_group_id')
                check(_text(group, 128) and all(r.get('related_group_id') == group for r in rows), 'SHARED_FIX_NOT_GROUPED', indices)
                check(len({r.get('pre_fix_commit') for r in rows if isinstance(r.get('pre_fix_commit'), str)}) <= 1,
                      'SHARED_FIX_PARENT_CONFLICT', indices)
            else:
                error('DUPLICATE_' + kind, indices)
            if kind == 'CASE_ID':
                snapshots = [(r.get('repo_id'), r.get('pre_fix_commit'), r.get('source_snapshot_id')) for r in rows]
                if any(s != snapshots[0] for s in snapshots):
                    error('CASE_SNAPSHOT_CONFLICT', indices)
    by_group = {}
    for i, r in valid:
        if r['related_group_id'] is not None:
            by_group.setdefault(r['related_group_id'], []).append((i, r))
    for group, rows in sorted(by_group.items()):
        check(len({r['repo_id'] for _, r in rows}) == 1, 'GROUP_CROSSES_REPOSITORIES', [i for i, _ in rows])
    if not errors:
        normalized_policy = deepcopy(p)
        for key in ('project_scope', 'accepted_issue_types', 'production_source_roots'):
            normalized_policy[key] = sorted(p[key])
        normalized_records = sorted(deepcopy(records), key=lambda r: r['case_id'])
        ph = hashlib.sha256(_bytes(normalized_policy)).hexdigest()
        report.update(status='VALID', policy_hash=ph,
                      registry_hash=hashlib.sha256(_bytes({'schema_version': 'dataset-registry-v1',
                          'policy_hash': ph, 'records': normalized_records})).hexdigest())
    return report
