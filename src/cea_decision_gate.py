"""Experimental report-only Selective Localization Decision Gate v0.2.

Opt in by explicitly invoking this module. Never changes localization results.
Development-inspired, independently unvalidated: REVIEW is not an error label;
STABLE_UNDER_TESTED_MASKS is not a correctness label or source certificate.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

MASKS = ('name', 'strings', 'calls')
LIMITS = [
    'REVIEW does not mean localization is incorrect.',
    'STABLE_UNDER_TESTED_MASKS does not mean localization is correct.',
    'Inspired by five development cases; no independently validated error-detection accuracy.',
    'Report-internal identity consistency only; source binding and report authenticity are not certified.',
    'Field information may remain in other features; no causal or confidence claim.',
]


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def ids(values, count):
    return (isinstance(values, list) and len(values) == count
            and all(isinstance(v, str) and bool(v.strip()) for v in values)
            and len(set(values)) == count)


def decide(report):
    """Pure postprocessing. Invalid anywhere takes precedence over REVIEW."""
    result = {'schema_version': 'cea-selective-localization-gate-v0.2',
              'status': 'UNVERIFIABLE', 'reason': None, 'original_top1_method_id': None,
              'triggered_interventions': [], 'interventions': [],
              'experimental': True, 'limitations': list(LIMITS)}
    try:
        require(isinstance(report, dict), 'REPORT_MISSING_OR_NOT_OBJECT')
        require(report.get('schema_version') == 'issuelens-cea-metadata-intervention-v1'
                and report.get('status') == 'OFFLINE_COUNTERFACTUAL_METADATA_DIAGNOSTIC_NOT_CORRECTNESS_PROOF',
                'UNSUPPORTED_OR_FAILED_CEA_REPORT')
        n = report.get('catalog_count')
        require(type(n) is int and n > 0 and report.get('topk') == 5
                and type(report.get('topk')) is int, 'INVALID_CATALOG_OR_TOPK')
        size = min(5, n)
        base = report.get('base_topk_method_ids')
        top20 = report.get('base_top20_method_ids')
        require(ids(base, size) and ids(top20, min(20, n)), 'INVALID_BASE_IDS')
        winner = report.get('base_top1_method_id')
        require(winner == base[0] and top20[:size] == base, 'BASE_IDENTITY_INCONSISTENT')
        require(report.get('masked_fields') == list(MASKS), 'MISSING_OR_UNSUPPORTED_MASKS')
        interventions = report.get('interventions')
        require(isinstance(interventions, list) and len(interventions) == 3, 'INCOMPLETE_INTERVENTIONS')
        by_field = {}
        for item in interventions:
            require(isinstance(item, dict), 'INVALID_INTERVENTION')
            field = item.get('masked_field')
            require(isinstance(field, str) and field in MASKS and field not in by_field, 'DUPLICATE_OR_UNKNOWN_MASK')
            by_field[field] = item
        details = []
        baseline_scores = None
        for field in MASKS:
            item = by_field[field]
            changed = item.get('top1_changed')
            top = item.get('intervened_topk_method_ids')
            new = item.get('new_top1_method_id')
            rank = item.get('original_top1_new_rank')
            require(type(changed) is bool and ids(top, size), 'INVALID_INTERVENTION_FIELDS')
            require(new == top[0] and changed == (new != winner), 'TOP1_IDENTITY_INCONSISTENT')
            require(integer(rank, 1, n) and changed == (rank != 1), 'TOP1_RANK_INCONSISTENT')
            require((rank <= size and top[rank-1] == winner) or
                    (rank > size and winner not in top), 'TOP1_POSITION_INCONSISTENT')
            overlap = item.get('topk_overlap_count')
            require(integer(overlap, 0, size) and overlap == len(set(base) & set(top)), 'OVERLAP_INCONSISTENT')
            for key in ('nonempty_channel_candidates', 'candidates_with_score_change'):
                require(integer(item.get(key), 0, n), 'INVALID_EFFECT_COUNT')
            require(integer(item.get('top20_overlap_count'), 0, min(20, n)), 'INVALID_TOP20_OVERLAP')
            require(item['top20_overlap_count'] >= overlap, 'TOP20_OVERLAP_INCONSISTENT')
            require(item['candidates_with_score_change'] <= item['nonempty_channel_candidates'],
                    'EFFECT_COUNT_INCONSISTENT')
            if item['candidates_with_score_change'] == 0:
                require(not changed and top == base and item['top20_overlap_count'] == min(20, n),
                        'ZERO_EFFECT_ORDER_INCONSISTENT')
            require(finite(item.get('original_top1_score_delta')), 'INVALID_SCORE_DELTA')
            moves = item.get('original_topk_moves')
            require(isinstance(moves, list) and len(moves) == size, 'INCOMPLETE_MOVES')
            scores = []
            positions = set()
            for original_rank, (mid, move) in enumerate(zip(base, moves), 1):
                require(isinstance(move, dict) and move.get('method_id') == mid
                        and type(move.get('original_rank')) is int and move['original_rank'] == original_rank,
                        'MOVE_IDENTITY_INCONSISTENT')
                position = move.get('intervened_rank')
                require(integer(position, 1, n) and position not in positions, 'INVALID_MOVE_RANK')
                positions.add(position)
                require(type(move.get('rank_shift')) is int and move['rank_shift'] == position-original_rank,
                        'MOVE_SHIFT_INCONSISTENT')
                require((position <= size and top[position-1] == mid) or
                        (position > size and mid not in top), 'MOVE_POSITION_INCONSISTENT')
                require(all(finite(move.get(k)) for k in ('original_score','intervened_score','score_drop')),
                        'INVALID_MOVE_SCORE')
                require(move['score_drop'] == move['original_score']-move['intervened_score'], 'MOVE_SCORE_INCONSISTENT')
                scores.append(move['original_score'])
            require(moves[0]['intervened_rank'] == rank
                    and moves[0]['score_drop'] == item['original_top1_score_delta'], 'WINNER_MOVE_INCONSISTENT')
            require(sum(move['score_drop'] != 0 for move in moves) <= item['candidates_with_score_change'],
                    'MOVE_EFFECT_COUNT_INCONSISTENT')
            require(baseline_scores is None or baseline_scores == scores, 'BASELINE_SCORES_INCONSISTENT')
            baseline_scores = scores
            details.append({'masked_field': field, 'top1_changed': changed,
                            'original_top1_method_id': winner, 'new_top1_method_id': new,
                            'original_top1_new_rank': rank, 'original_top1_rank_shift': rank-1,
                            'top5_overlap_count': overlap, 'top5_effective_size': size,
                            'top5_overlap': overlap/size})
        triggers = [d['masked_field'] for d in details if d['top1_changed']]
        result.update(status='REVIEW' if triggers else 'STABLE_UNDER_TESTED_MASKS',
                      original_top1_method_id=winner, triggered_interventions=triggers, interventions=details)
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        result['reason'] = str(exc)
    return result


def parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('NONFINITE_JSON_NUMBER')
    return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique, parse_constant=invalid)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--output', type=Path, help='new JSON file only; default: stdout')
    args = parser.parse_args()
    # Refuse overwrites before reading input. This command never modifies the report.
    if args.output and (args.output.exists() or args.output.resolve() == args.report.resolve()):
        parser.exit(2, 'OUTPUT_ALREADY_EXISTS_OR_IS_INPUT\n')
    raw = None
    try:
        require(not any('private' in p.lower() for p in args.report.resolve().parts), 'PRIVATE_INPUT_FORBIDDEN')
        raw = args.report.read_bytes()
        result = decide(parse(raw))
    except (OSError, ValueError) as exc:
        result = decide(None)
        result['reason'] = str(exc)
    result['report_sha256'] = hashlib.sha256(raw).hexdigest() if raw is not None else None
    output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if args.output:
        with args.output.open('x', encoding='utf-8') as stream:
            stream.write(output)
    else:
        print(output, end='')


if __name__ == '__main__':
    main()
