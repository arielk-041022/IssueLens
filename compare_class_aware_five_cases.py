"""Read-only, post-hoc: frozen BM25 Top20/40 vs independent class-aware Top40.

Run from IssueLens repository root. Requires existing class_aware_expansion_v1.py.
No model calls, no writes, no tuning. This is a DIAGNOSTIC, not blinded evaluation.
"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path.cwd().resolve()
EXP = ROOT / 'experiments' / 'fair-comparison-v1.1-admission-v1'
ENV = EXP / 'prediction-environment'
POOL = EXP / 'reranker-input'
PILOT = ROOT / 'experiments' / 'fair-comparison-commons-lang-pilot-v1'
EXPAND_FILE = ROOT / 'experiments' / 'class-aware-expansion-v1' / 'class_aware_expansion_v1.py'
CASES = ('LANG-1745', 'LANG-1750', 'LANG-1753', 'LANG-1757', 'LANG-1759')


def demand(ok, reason):
    if not ok:
        raise RuntimeError('FAIL_CLOSED: ' + reason)


def load(path):
    return json.loads(path.read_bytes())


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def seal_check(root, binding):
    manifest = root / 'manifest.sha256.json'
    raw = manifest.read_bytes()
    demand(sha(raw) == binding['manifest_sha256'], 'GT_MANIFEST_SHA')
    files = json.loads(raw)['files']
    demand(len(files) == binding['verified_files'], 'GT_FILE_COUNT')
    for relative, digest in files.items():
        path = (root / relative).resolve()
        demand(path.is_relative_to(root) and path.is_file(), 'GT_FILE_PATH:' + relative)
        demand(sha(path.read_bytes()) == digest, 'GT_FILE_SEAL:' + relative)
    return len(files)


def count(ids, truth):
    return len(set(ids) & truth)


def main():
    demand(EXPAND_FILE.is_file(), 'EXPANDER_MODULE_NOT_FOUND')
    demand(ENV.is_dir() and POOL.is_dir(), 'RUN_FROM_REPOSITORY_ROOT')
    binding = load(PILOT / 'private-gt-binding.json')
    gtroot = Path(binding['private_root']).resolve()
    demand(gtroot.is_dir(), 'PRIVATE_GT_ROOT_MISSING')
    verified = seal_check(gtroot, binding)

    # Load exact frozen ranking implementation, keeping expanding code separate.
    sys.path[:0] = [str(ENV / 'fair-runtime'), str(ENV / 'runtime' / 'src'), str(ENV / 'source-occurrence-compat-v1')]
    import compat
    import bm25_baseline
    from pipeline import shortlist
    from core import canonical

    spec = importlib.util.spec_from_file_location('class_aware_expansion_v1', EXPAND_FILE)
    demand(spec is not None and spec.loader is not None, 'EXPANDER_IMPORT_SPEC')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    demand(callable(getattr(mod, 'expand', None)), 'EXPANDER_MISSING_EXPAND')

    entries = {x['case_id']: x for x in load(ENV / 'cases.json')['cases']}
    for case in CASES:
        demand(case in binding['case_ids'] and case in entries, 'UNREGISTERED_CASE:' + case)
        row = entries[case]
        raw_cat = (ENV / row['catalog']).read_bytes()
        raw_issue = (ENV / row['public_input']).read_bytes()
        demand(sha(raw_cat) == row['catalog_sha256'], 'CATALOG_SHA:' + case)
        demand(sha(raw_issue) == row['input_sha256'], 'ISSUE_SHA:' + case)
        pool = load(POOL / case / 'pool.json')
        demand(pool['catalog_sha256'] == sha(raw_cat), 'POOL_CATALOG_SHA:' + case)
        demand(pool['input_sha256'] == sha(raw_issue), 'POOL_ISSUE_SHA:' + case)
        catalog = compat.load(raw_cat)
        issue = json.loads(raw_issue)
        top, trace = shortlist(catalog, issue['issue_text'], bm25_baseline)
        order = trace['full_ranking_ids']
        demand(sha(canonical(order)) == pool['full_order_sha256'], 'FULL_ORDER_SHA:' + case)
        demand(trace['documents_tokens_sha256'] == pool['documents_tokens_sha256'], 'DOC_TOKENS_SHA:' + case)
        demand(top == pool['top20'] == order[:20], 'TOP20_SHA_OR_ORDER:' + case)
        catalog_ids = {x['occurrence_id'] for x in catalog['records']}
        demand(len(order) == len(set(order)) == len(catalog_ids) and set(order) == catalog_ids,
               'FULL_ORDER_CATALOG_CONSISTENCY:' + case)

        # Run independent expansion on public inputs only, BEFORE opening mapped GT.
        source_root = ENV / row['source_root']
        demand(source_root.is_dir(), 'SOURCE_ROOT_NOT_FOUND:' + case)
        expanded = mod.expand(list(pool['top20']), catalog, source_root)
        items = expanded['candidates']
        expanded_ids = [x['occurrence_id'] for x in items]
        demand(20 <= len(items) <= 40 and len(set(expanded_ids)) == len(items), 'EXPANSION_COUNT_OR_DUPLICATE:' + case)
        demand(expanded_ids[:20] == pool['top20'], 'EXPANSION_CHANGED_FROZEN_TOP20:' + case)
        demand(set(expanded_ids).issubset(catalog_ids), 'EXPANSION_UNKNOWN_ID:' + case)
        demand(expanded.get('original_count') == 20 and expanded.get('expanded_count') == len(items) - 20,
               'EXPANSION_INCONSISTENT_COUNTS:' + case)
        demand(all(x['structural_relation'] == 'ORIGINAL_TOP20' for x in items[:20]),
               'ORIGINAL_REASON_MISMATCH:' + case)
        demand(all(x['structural_relation'] in ('SAME_CLASS_OVERLOAD', 'SAME_CLASS_SOURCE_NEIGHBOR')
                   for x in items[20:]), 'ADDED_REASON_MISMATCH:' + case)

        # GT is used solely for post-hoc scoring, never in ranking/expansion.
        gt = load(gtroot / 'mapped' / f'{case}.json')
        demand(gt['case_id'] == case, 'GT_CASE_ID:' + case)
        demand(gt['mapped_count'] == gt['main_GT_count'] == len(gt['main_GT']), 'GT_COUNTS:' + case)
        truth = {g['occurrence_id'] for g in gt['main_GT']}
        demand(len(truth) == len(gt['main_GT']) and truth <= catalog_ids, 'GT_ID_SET:' + case)
        k20, k40, kexp = count(order[:20], truth), count(order[:40], truth), count(expanded_ids, truth)
        extra = set(expanded_ids[20:]) & truth
        new_over_bm25 = (set(expanded_ids) & truth) - set(order[:40])
        ranks = {occ: i + 1 for i, occ in enumerate(order)}
        print(f'=== {case} | EXPLORATORY_POST_HOC_ONLY ===')
        print(f'SEALS=VERIFIED; BM25_FULL_ORDER=VERIFIED; ORIGINAL_TOP20=UNCHANGED; GT={len(truth)}')
        print(f'BM25_Top20={k20}/{len(truth)} ({k20/len(truth):.1%})')
        print(f'BM25_Top40={k40}/{len(truth)} ({k40/len(truth):.1%})')
        print(f'ClassAware_Top40={kexp}/{len(truth)} ({kexp/len(truth):.1%})')
        print(f'ClassAware_minus_BM25_Top40={kexp-k40:+d} GT methods; BM25 uses 40 slots, expansion uses {len(items)}')
        print(f'ClassAware_GT_not_in_BM25_Top40={len(new_over_bm25)}; rank_positions={sorted(ranks[i] for i in new_over_bm25)}')
        print(f'Added_GT={len(extra)}/{len(items)-20}; BM25_original_ranks_of_added_GT={sorted(ranks[i] for i in extra)}')
        print(f'Expansion_diagnostics: unverified_seeds={expanded["unverified_seed_count"]}, rejected_extensions={expanded["rejected_unverified_extensions"]}')
        relation = {}
        for x in items[20:]:
            r = x['structural_relation']
            relation[r] = relation.get(r, 0) + 1
        print('Added_relation_counts=' + json.dumps(relation, sort_keys=True))
        print('NOTE: Candidate Recall only; NOT final LLM Top5; not a blind test')
    print(f'CHECKS=GT_MANIFEST_VERIFIED_{verified}_FILES,FROZEN_RANKING_SHA,TOP20_INVARIANT; NO_API; NO_WRITES; NO_GT_TUNING')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(2)
