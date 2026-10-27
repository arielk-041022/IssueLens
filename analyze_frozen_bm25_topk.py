"""Post-hoc frozen-BM25 Top-K ceiling analysis; read-only, no network.
Run in IssueLens repository root. GT is used ONLY after ranking and seals verify.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path.cwd().resolve()
EXP = ROOT / 'experiments' / 'fair-comparison-v1.1-admission-v1'
ENV = EXP / 'prediction-environment'
POOL = EXP / 'reranker-input'
PILOT = ROOT / 'experiments' / 'fair-comparison-commons-lang-pilot-v1'
CASES = ('LANG-1745', 'LANG-1750')
KS = (20, 40, 80, 160)


def require(ok, why):
    if not ok:
        raise RuntimeError('FAIL_CLOSED: ' + why)


def read_json(path):
    return json.loads(path.read_bytes())


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def verify_manifest(manifest_path, root, expected_sha=None):
    raw = manifest_path.read_bytes()
    if expected_sha:
        require(sha(raw) == expected_sha, 'GT_MANIFEST_SHA_MISMATCH')
    items = json.loads(raw)['files']
    for rel, expected in items.items():
        p = (root / rel).resolve()
        require(p.is_relative_to(root.resolve()) and p.is_file(), 'MANIFEST_FILE_MISSING_OR_UNSAFE:' + rel)
        require(sha(p.read_bytes()) == expected, 'MANIFEST_FILE_HASH:' + rel)
    return len(items)


def main():
    require(ENV.is_dir() and POOL.is_dir() and PILOT.is_dir(), 'RUN_FROM_PROJECT_ROOT')
    binding = read_json(PILOT / 'private-gt-binding.json')
    gtroot = Path(binding['private_root']).resolve()
    require(gtroot.is_dir(), 'GT_ROOT_MISSING')
    verified = verify_manifest(gtroot / 'manifest.sha256.json', gtroot, binding['manifest_sha256'])
    require(verified == binding['verified_files'], 'GT_MANIFEST_COUNT')

    # Import exact frozen implementation, not the current source tree's evolving BM25.
    sys.path[:0] = [str(ENV / 'fair-runtime'), str(ENV / 'runtime' / 'src'), str(ENV / 'source-occurrence-compat-v1')]
    import compat
    import bm25_baseline
    from pipeline import shortlist
    from core import canonical

    for case in CASES:
        require(case in binding['case_ids'], 'CASE_NOT_GT_REGISTERED:' + case)
        row = next((x for x in read_json(ENV / 'cases.json')['cases'] if x['case_id'] == case), None)
        require(row is not None, 'CASE_NOT_FOUND:' + case)
        raw_cat = (ENV / row['catalog']).read_bytes()
        raw_input = (ENV / row['public_input']).read_bytes()
        require(sha(raw_cat) == row['catalog_sha256'], 'CATALOG_HASH:' + case)
        require(sha(raw_input) == row['input_sha256'], 'INPUT_HASH:' + case)
        catalog = compat.load(raw_cat)
        issue = json.loads(raw_input)
        pool = read_json(POOL / case / 'pool.json')
        require(pool['catalog_sha256'] == sha(raw_cat), 'POOL_CATALOG_HASH:' + case)
        require(pool['input_sha256'] == sha(raw_input), 'POOL_INPUT_HASH:' + case)
        top, trace = shortlist(catalog, issue['issue_text'], bm25_baseline)
        order = trace['full_ranking_ids']
        require(sha(canonical(order)) == pool['full_order_sha256'], 'FULL_RANKING_HASH:' + case)
        require(trace['documents_tokens_sha256'] == pool['documents_tokens_sha256'], 'TOKENIZED_DOCS_HASH:' + case)
        require(top == pool['top20'], 'FROZEN_TOP20_MISMATCH:' + case)
        require(order[:20] == pool['top20'], 'FULL_RANKING_TOP20_MISMATCH:' + case)
        require(len(order) == len(set(order)), 'DUPLICATED_RANKING_IDS:' + case)
        require(set(order) == {r['occurrence_id'] for r in catalog['records']}, 'RANKING_CATALOG_SET_MISMATCH:' + case)

        # GT accessed ONLY after frozen prediction order has passed all hashes.
        gt = read_json(gtroot / 'mapped' / (case + '.json'))
        require(gt['case_id'] == case, 'GT_CASE_ID:' + case)
        require(gt['mapped_count'] == gt['main_GT_count'] == len(gt['main_GT']), 'GT_COUNT:' + case)
        ids = [x['occurrence_id'] for x in gt['main_GT']]
        require(all(isinstance(x, str) and x for x in ids), 'GT_ID_MISSING:' + case)
        require(len(ids) == len(set(ids)), 'DUPLICATED_GT_IDS:' + case)
        require(set(ids).issubset(set(order)), 'GT_ID_OUTSIDE_CATALOG:' + case)
        gtset = set(ids)
        rank = {occ: i+1 for i, occ in enumerate(order)}
        positions = sorted(rank[g] for g in gtset)
        print(f'=== {case} | POST_HOC_TOPK_DIAGNOSTIC ===')
        print(f'FULL_ORDER_SHA256=VERIFIED; TOP20=VERIFIED; CATALOG={len(order)}; GT={len(gtset)}')
        for k in KS:
            hit = sum(r <= k for r in positions)
            print(f'Recall@{k}={hit}/{len(gtset)} ({hit/len(gtset):.1%})')
        print('GT_rank_positions=' + ','.join(map(str, positions)))
        print('NOTE=Retrieval coverage only; not LLM Top-5 accuracy')
    print(f'GT_MANIFEST=VERIFIED ({verified} files); NO_API; NO_WRITES; POST_HOC_ONLY; NO_PARAMETER_TUNING')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(2)
