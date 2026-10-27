"""Read-only post-hoc E2E replay of two frozen DeepSeek responses under output contract V2.
Run in IssueLens root: python -B test_pilot_v2_offline_e2e.py
No API, GT, pricing or scoring; no persistent writes.
"""
import json
import sys
from pathlib import Path
from hashlib import sha256

from pilot_output_contract_v2_adapter import adapt_final_v2

ROOT = Path(__file__).resolve().parent
EXEC = ROOT / 'experiments/fair-comparison-commons-lang-pilot-v1/executor'
JOBS = (
    ('LANG-1745', 'formal-recovery-v1', 7, 'RANKING_COUNT'),
    ('LANG-1750', 'formal-supplement-v1', 12, 'UNKNOWN_OR_AMBIGUOUS_ID:0'),
)


def read_json(file):
    return json.loads(file.read_bytes())


def main():
    if not (EXEC / 'binding.json').is_file():
        raise FileNotFoundError('Place script in the IssueLens project root.')
    sys.path.insert(0, str(EXEC / 'runtime'))
    from engine import decode_response  # Frozen validation function: import only; no execute

    binding = read_json(EXEC / 'binding.json')
    cases = {c['case_id']: c for c in read_json(EXEC / 'cases.json')['cases']}
    policy = read_json(EXEC / 'protocol.json')
    ok = 0
    for case, batch, step, failure in JOBS:
        root = EXEC / 'output' / batch / case / 'B_DIRECT'
        response_path = root / f's{step}.response.bin'
        raw = response_path.read_bytes()
        meta = read_json(root / f's{step}.http.json')
        status = read_json(root / 'status.json')
        if meta.get('response_sha256') != sha256(raw).hexdigest():
            raise AssertionError(f'{case}: response hash mismatch')
        if status.get('status') != 'PROTOCOL_OR_RESOURCE_FAILED' or status.get('failure_reason') != failure:
            raise AssertionError(f'{case}: original formal failure changed')
        env, obj = decode_response(meta, raw)
        usage = env.get('usage')
        max_tokens = policy['model_parameters']['max_tokens']
        budget = policy['budget']
        if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')):
            raise AssertionError(f'{case}: usage unavailable')
        if usage['prompt_tokens'] + usage['completion_tokens'] != usage['total_tokens'] or usage['completion_tokens'] > max_tokens:
            raise AssertionError(f'{case}: per-response token check failed')
        if status['provider_input_tokens'] > budget['max_cumulative_input_tokens'] or status['provider_output_tokens'] > budget['max_cumulative_output_tokens']:
            raise AssertionError(f'{case}: recorded cumulative budget exceeded')
        catalog = read_json(Path(binding['parent_public_root']) / cases[case]['catalog'])
        allowed = {rec['occurrence_id'] for rec in catalog['records']}
        adapted = adapt_final_v2(obj, catalog, allowed, direct=True)
        print(f'=== {case} / {batch} ===')
        print('original_status:', status['status'], '| reason:', status['failure_reason'])
        print('envelope: VERIFIED | recorded_budget: WITHIN_LIMIT | V2:', adapted.status)
        if adapted.status == 'ACCEPTED':
            result = adapted.result
            print('ranking_length:', len(result['ranking']), '| mapping_modes:', ','.join(m['mode'] for m in result['identity_mapping']))
            print('canonical_IDs:')
            for i, oid in enumerate(result['ranking'], 1):
                print(f' {i}. {oid}')
            ok += 1
        else:
            print('V2_failure:', adapted.failure_reason)
    print(f'\nPOST_HOC_V2_ACCEPTED={ok}/{len(JOBS)}; ORIGINAL_RESULTS_UNCHANGED; GT_READ=NO; API_CALLS=0; FILE_WRITES=0')
    return 0 if ok == len(JOBS) else 1


if __name__ == '__main__':
    raise SystemExit(main())
