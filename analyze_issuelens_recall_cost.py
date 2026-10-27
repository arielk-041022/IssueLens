"""Read-only GT Top-20 coverage, rerank loss, and observed DeepSeek resource audit.
Run from the IssueLens repo root; no API, output writes, or evaluation mutations.
Observational comparison only: A is one-shot and Direct has tools and multiple calls.
"""
import json
import sys
from pathlib import Path

from score_issuelens_pilot_posthoc_v2 import demand, digest, load, verify_manifest, as_rank

ROOT = Path(__file__).resolve().parent
PILOT = ROOT / 'experiments/fair-comparison-commons-lang-pilot-v1'
EXEC = PILOT / 'executor'
BATCHES = ('formal-recovery-v1', 'formal-supplement-v1')
CASES = ('LANG-1745', 'LANG-1750')
ARMS = ('A_PLAIN', 'A_EVIDENCE', 'B_DIRECT', 'B_ISSUELENS')


def resolve_top20(pool_data, catalog):
    items = pool_data['top20']
    demand(type(items) is list and len(items) == 20, 'Frozen Top-20 is missing or not length 20')
    records = catalog['records']
    result = []
    for item in items:
        if type(item) is str:
            oid = item
        elif type(item) is int:
            demand(0 <= item < len(records), 'Top-20 index out of range')
            oid = records[item]['occurrence_id']
        elif type(item) is dict:
            if type(item.get('occurrence_id')) is str:
                oid = item['occurrence_id']
            elif type(item.get('ordinal')) is int:
                ordinal = item['ordinal']
                demand(0 <= ordinal < len(records), 'Top-20 ordinal out of range')
                oid = records[ordinal]['occurrence_id']
            else:
                raise ValueError('Unrecognized frozen Top-20 record fields: ' + ', '.join(item))
        else:
            raise ValueError('Unrecognized Top-20 entry type: ' + type(item).__name__)
        result.append(oid)
    demand(len(set(result)) == 20, 'Duplicate occurrence IDs in frozen Top-20')
    known = {r['occurrence_id'] for r in records}
    demand(all(oid in known for oid in result), 'Unknown Top-20 occurrence ID')
    return result


def describe_cost(status):
    observations = status.get('model_observations') or []
    attempts = status.get('wire_attempts')
    demand(type(attempts) is int and attempts >= 1, 'Missing positive wire attempt count')
    demand(len(observations) == attempts, 'Per-request observations missing')
    complete = sum(1 for o in observations if o.get('transport_status') == 'HTTP_COMPLETE' and o.get('http_status') == 200)
    prompt = sum(o.get('usage', {}).get('prompt_tokens', 0) for o in observations)
    answer = sum(o.get('usage', {}).get('completion_tokens', 0) for o in observations)
    demand(prompt == status['provider_input_tokens'] and answer == status['provider_output_tokens'], 'Provider token accounting mismatch')
    return f'calls={attempts}, HTTP200={complete}/{attempts}, input={prompt}, output={answer}, total={prompt+answer}, tools={status.get("tool_operations", 0)}, seconds={status.get("arm_seconds", 0):.2f}'


def main():
    binding = load(EXEC / 'binding.json')
    cases = {x['case_id']: x for x in load(EXEC / 'cases.json')['cases']}
    gb = load(PILOT / 'private-gt-binding.json')
    gtroot = Path(gb['private_root'])
    demand(verify_manifest(gtroot, gtroot / 'manifest.sha256.json', gb['manifest_sha256']) == gb['verified_files'], 'Private GT manifest verification failed')
    demand(gb['GT_methods'] == gb['exact_mapped'] and gb['UNRESOLVED'] == 0, 'GT mapping incomplete')
    demand(all(case in gb['case_ids'] and case in cases for case in CASES), 'Case missing from registered GT')

    stored = {}
    for batch in BATCHES:
        base = EXEC / 'output' / batch
        verify_manifest(base, base / 'manifest.sha256.json')
        for row in load(base / 'summary.json')['results']:
            if row['status'] == 'NOT_STARTED_AFTER_FAILURE':
                continue
            key = (row['case_id'], row['arm'])
            demand(key not in stored, f'Duplicate execution: {key}')
            path = base / row['case_id'] / row['arm']
            verify_manifest(path, path / 'manifest.sha256.json')
            status = load(path / 'status.json')
            demand(status['status'] == row['status'], 'Recorded status mismatch')
            stored[key] = status

    pool = Path(binding['pool_root'])
    pool_manifest = pool / 'manifest.sha256.json'
    if 'pool_manifest_sha256' in binding:
        verify_manifest(pool, pool_manifest, binding['pool_manifest_sha256'])
    else:
        verify_manifest(pool, pool_manifest)

    for case in CASES:
        row = cases[case]
        catalogpath = Path(binding['parent_public_root']) / row['catalog']
        demand(digest(catalogpath) == row['catalog_sha256'], f'Public catalog changed for {case}')
        catalog = load(catalogpath)
        doc = load(gtroot / 'mapped' / f'{case}.json')
        demand(doc['case_id'] == case and len(doc['main_GT']) == doc['main_GT_count'] == doc['mapped_count'], 'GT count mismatch')
        truth = {x['occurrence_id'] for x in doc['main_GT']}
        top20 = resolve_top20(load(pool / case / 'pool.json'), catalog)
        poolhits = truth.intersection(top20)
        print(f'\n=== {case} ===')
        print(f'GT={len(truth)}; frozen_Top20_GT={len(poolhits)}/{len(truth)} ({len(poolhits)/len(truth):.1%}); '
              f'GT_outside_Top20={len(truth-poolhits)}; Top5_recall_ceiling={min(len(poolhits),5)}/{len(truth)}')
        for arm in ARMS:
            status = stored.get((case, arm))
            if status is None:
                print(f'{arm}: NOT_STARTED')
                continue
            r = as_rank(status)
            if r is None:
                print(f'{arm}: {status["status"]} / {status.get("failure_reason")}; {describe_cost(status)}')
            else:
                overlap = truth.intersection(r)
                if arm != 'B_DIRECT':
                    demand(set(r).issubset(set(top20)), f'{arm} returned ID outside Top20')
                    missed = poolhits.difference(r)
                    print(f'{arm}: GT_in_Top5={len(overlap)}/{len(truth)}; '
                          f'GT_in_pool_but_not_Top5={len(missed)}; {describe_cost(status)}')
                else:
                    print(f'{arm}: GT_in_Top5={len(overlap)}/{len(truth)}; {describe_cost(status)}')
        print('NOTE: Top-20 coverage is retrieval ceiling; Top-5 may be limited by five-slot capacity.')
    print('\nCHECKS=GT_SEALED,PREDICTIONS_SEALED,CATALOG_SHA,POOL_MANIFEST; NO_API; NO_WRITES; NO_GT_TUNING')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'FAIL_CLOSED: {type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(2)
