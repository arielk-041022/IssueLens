"""Read-only independent scoring of frozen 5-case pilot and post-hoc V2 direct responses.

Run from IssueLens repository root:
    python -B score_issuelens_pilot_posthoc_v2.py

No network, LLM calls, writes, GT leakage to executor, or prediction changes.
Original V1 protocol failures remain failures. V2 is diagnostic ONLY.
"""
import hashlib
import json
import sys
from pathlib import Path

from pilot_output_contract_v2_adapter import adapt_final_v2

ROOT = Path(__file__).resolve().parent
PILOT = ROOT / 'experiments/fair-comparison-commons-lang-pilot-v1'
EXEC = PILOT / 'executor'
BATCHES = ('formal-recovery-v1', 'formal-supplement-v1')
ARMS = ('A_PLAIN', 'A_EVIDENCE', 'B_DIRECT', 'B_ISSUELENS')
V2_CASES = {'LANG-1745': ('formal-recovery-v1', 7, 'RANKING_COUNT'),
            'LANG-1750': ('formal-supplement-v1', 12, 'UNKNOWN_OR_AMBIGUOUS_ID:0')}


def demand(condition, message):
    if not condition:
        raise RuntimeError(message)


def load(path):
    return json.loads(path.read_bytes())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_manifest(base, manifest_file, expected=None):
    demand(manifest_file.is_file(), f'Missing manifest: {manifest_file}')
    if expected is not None:
        demand(digest(manifest_file) == expected, f'Manifest SHA mismatch: {manifest_file}')
    manifest = load(manifest_file)
    demand(type(manifest) is dict and type(manifest.get('files')) is dict, f'Invalid manifest: {manifest_file}')
    for rel, sha in manifest['files'].items():
        p = (base / rel).resolve()
        demand(p.is_relative_to(base.resolve()), f'Path escapes manifest root: {rel}')
        demand(p.is_file() and digest(p) == sha, f'File SHA mismatch: {rel}')
    return len(manifest['files'])


def metrics(ranking, truth):
    positions = [i for i, c in enumerate(ranking[:5], 1) if c in truth]
    first = min(positions) if positions else None
    return (int(first == 1), int(first is not None), 0.0 if first is None else 1.0/first,
            len(set(ranking[:5]) & truth), len(truth))


def as_rank(status):
    if status.get('status') != 'VALID':
        return None
    result = status.get('result')
    demand(type(result) is dict and type(result.get('ranking')) is list, 'VALID without ranking')
    ranking = result['ranking']
    demand(1 <= len(ranking) <= 5 and len(set(ranking)) == len(ranking), 'Invalid VALID ranking')
    return ranking


def render(label, ranks, gts):
    if not ranks:
        print(f'{label}: no scored predictions')
        return
    scores = [metrics(rank, gts[case]) for case, rank in ranks]
    n = len(scores)
    fmt = lambda x: f'{x:.4f}'
    recall_hits = sum(x[3] for x in scores)
    recall_total = sum(x[4] for x in scores)
    print(f'{label}: n={n}, Hit@1={sum(x[0] for x in scores)}/{n}, '
          f'Hit@5={sum(x[1] for x in scores)}/{n}, '
          f'MRR@5={fmt(sum(x[2] for x in scores)/n)}, '
          f'MethodRecall@5={recall_hits}/{recall_total} ({fmt(recall_hits/recall_total)})')
    for (case, rank), score in zip(ranks, scores):
        print(f'  {case}: returned={len(rank)}, GT={score[4]}, Hit1={score[0]}, Hit5={score[1]}, '
              f'firstRR={fmt(score[2])}, GT_methods_hit={score[3]}')


def main():
    demand((EXEC / 'binding.json').is_file(), 'Place script in IssueLens project root')
    binding = load(EXEC / 'binding.json')
    cases = {r['case_id']: r for r in load(EXEC / 'cases.json')['cases']}
    gt_binding = load(PILOT / 'private-gt-binding.json')
    gt_root = Path(gt_binding['private_root'])
    gt_manifest = gt_root / 'manifest.sha256.json'
    n_verified = verify_manifest(gt_root, gt_manifest, gt_binding['manifest_sha256'])
    demand(n_verified == gt_binding['verified_files'], 'GT file count differs from binding')
    registered = gt_binding['case_ids']
    demand(set(registered) == set(cases), 'Registered cases differ')
    demand(gt_binding['GT_methods'] == gt_binding['exact_mapped'] and gt_binding['UNRESOLVED'] == 0, 'GT mapping not complete')
    print(f'GT_SEAL_VERIFIED: files={n_verified}; cases={len(registered)}; methods={gt_binding["GT_methods"]}')

    # First verify BOTH full batch manifests and all per-arm manifests; do not score half-trusted predictions.
    stored = {}
    for batch in BATCHES:
        base = EXEC / 'output' / batch
        demand((base / 'summary.json').is_file(), f'Missing batch summary: {batch}')
        verify_manifest(base, base / 'manifest.sha256.json')
        summary = load(base / 'summary.json')
        for row in summary['results']:
            case, arm, state = row['case_id'], row['arm'], row['status']
            demand(case in cases and arm in ARMS, f'Unknown case/arm in {batch}')
            if state == 'NOT_STARTED_AFTER_FAILURE':
                continue
            key = (case, arm)
            demand(key not in stored, f'Duplicate executed case/arm: {key}')
            work = base / case / arm
            verify_manifest(work, work / 'manifest.sha256.json')
            detail = load(work / 'status.json')
            demand(detail['status'] == state, f'Status disagreement: {key}')
            stored[key] = (batch, detail)
    print(f'PREDICTION_SEALS_VERIFIED: executed={len(stored)}/20; not_started={20-len(stored)}')
    demand(len(stored) == 7, f'Unexpected executed count {len(stored)} (this script targets the recorded two batches)')

    # Verify public catalog hashes independently from private GT before reading any method labels.
    catalogs = {}
    for case, row in cases.items():
        catpath = Path(binding['parent_public_root']) / row['catalog']
        demand(digest(catpath) == row['catalog_sha256'], f'Catalog SHA mismatch: {case}')
        catalogs[case] = load(catpath)

    # Ground truth is read only after all seals, contexts and predictions are validated.
    truths = {}
    for case in registered:
        doc = load(gt_root / 'mapped' / f'{case}.json')
        demand(doc['case_id'] == case and doc['main_GT_count'] == doc['mapped_count'] == len(doc['main_GT']), f'GT count mismatch: {case}')
        truth = [x['occurrence_id'] for x in doc['main_GT']]
        demand(all(type(x) is str and x for x in truth) and len(truth) == len(set(truth)), f'GT IDs invalid: {case}')
        demand(len(truth) > 0, f'No GT methods: {case}')
        truths[case] = set(truth)
    demand(sum(map(len, truths.values())) == gt_binding['GT_methods'], 'GT total differs from binding')

    print('\n=== V1 FROZEN FORMAL (no rewrites; failures are misses, NOT_STARTED excluded) ===')
    for arm in ARMS:
        relevant = [(case, as_rank(stored[(case, arm)][1]))
                    for case in registered if (case, arm) in stored]
        rank_with_fails_as_empty = [(case, rank if rank is not None else []) for case, rank in relevant]
        render(arm, rank_with_fails_as_empty, truths)
        print(f'  valid={sum(rank is not None for _, rank in relevant)}, '
              f'protocol_failed={sum(rank is None for _, rank in relevant)}, '
              f'not_started={len(registered)-len(relevant)}')

    print('\n=== V2 POST-HOC DIAGNOSTIC (NOT a V1 official score) ===')
    for case, (batch, step, reason) in V2_CASES.items():
        demand(stored[(case, 'B_DIRECT')][0] == batch, 'V2 source batch changed')
        status = stored[(case, 'B_DIRECT')][1]
        demand(status['status'] == 'PROTOCOL_OR_RESOURCE_FAILED' and status['failure_reason'] == reason,
               f'Original V1 Direct failure has changed: {case}')
        p = EXEC / 'output' / batch / case / 'B_DIRECT'
        raw = (p / f's{step}.response.bin').read_bytes()
        meta = load(p / f's{step}.http.json')
        demand(meta['response_sha256'] == hashlib.sha256(raw).hexdigest(), 'Response digest mismatch')
        demand(meta.get('transport_status') == 'HTTP_COMPLETE' and meta.get('http_status') == 200, 'Noncomplete HTTP')
        envelope = json.loads(raw)
        demand(envelope['choices'][0]['finish_reason'] == 'stop', 'Final response did not stop')
        obj = json.loads(envelope['choices'][0]['message']['content'])
        allowed = {r['occurrence_id'] for r in catalogs[case]['records']}
        mapped = adapt_final_v2(obj, catalogs[case], allowed, direct=True)
        demand(mapped.status == 'ACCEPTED', f'V2 rejected: {case}: {mapped.failure_reason}')
        render(f'{case} / V2 only', [(case, mapped.result['ranking'])], truths)
        print(f'  V1 remains {status["status"]} ({reason}); V2 mapping not written to prediction')
    print('\nREAD_ONLY=TRUE; API_CALLS=0; PREDICTIONS_UNCHANGED=TRUE; V2=POST_HOC_ONLY')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'FAIL_CLOSED: {type(exc).__name__}: {exc}', file=sys.stderr)
        raise SystemExit(2)
