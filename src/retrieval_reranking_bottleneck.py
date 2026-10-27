"""Read-only analysis of frozen Top-50 pools; no retrieval/model execution."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / 'experiments/top50-rerank-live-v2'
HIER = ROOT / 'experiments/hierarchical-retrieval-v1'
COMPACT = ROOT / 'experiments/top50-compact-preflight-v1'
OUT = ROOT / 'experiments/retrieval-reranking-bottleneck-v1'
PINS = {
    LIVE: ('final-manifest.sha256.json', 'a9432ce18affd20b135ab8e5fdbda7c8d79daa9a728285fc2bf47e56645ff8c5'),
    HIER: ('manifest.sha256.json', 'bc084fd1e2bf168e6cd8dd2bfd1c8115300ca50008c7581fdd543590f9c6cfc9'),
    COMPACT: ('manifest.sha256.json', 'b241a3b771d7c107036cef0fcd231dd18917d3a14027b757fe97385059ce57dc'),
}
READ = {}


def need(ok, reason):
    if not ok:
        raise ValueError(reason)


def raw(path):
    content = path.read_bytes(); digest = hashlib.sha256(content).hexdigest()
    need(str(path) not in READ or READ[str(path)] == digest, 'INPUT_CHANGED')
    READ[str(path)] = digest
    return content


def load(path):
    return json.loads(raw(path))


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value


def identities(values):
    need(all(isinstance(v, str) and v.startswith('java-source-v2:') and '#' in v for v in values),
         'UNVERIFIABLE_METHOD_ID')
    need(len(values) == len(set(values)), 'METHOD_IDENTITY_CONFLICT')
    return set(values)


def analyze(truth, pool, final, status):
    """Counts are disjoint except raw pool misses, which include capacity loss."""
    candidates = identities(pool)
    result = dict(status=status, GT_count=None, pool_GT=None, final_hits=None, theoretical_top5=None,
                  gap=None, retrieval_missed=None, pool_GT_not_in_top5=None, capacity_limit=None,
                  avoidable_reranking_gap=None, labels=[])
    if truth is None:
        result['labels'] = ['LABEL_UNKNOWN']; return result
    gt = identities(truth); need(bool(gt), 'EMPTY_GT_IS_NOT_A_LABEL')
    result['GT_count'] = len(gt)
    if status not in ('VALID', 'OUTPUT_PROTOCOL_FAILED'):
        result['labels'] = ['NOT_EVALUABLE:' + status]; return result
    present = gt & candidates
    result.update(pool_GT=len(present), retrieval_missed=len(gt - candidates),
                  theoretical_top5=min(len(present), 5), capacity_limit=max(len(present) - 5, 0),
                  retrieval_missed_method_ids=sorted(gt - candidates))
    if result['retrieval_missed']:
        result['labels'].append('RETRIEVAL_OMISSION')
    if result['capacity_limit']:
        result['labels'].append('TOP5_CAPACITY_LIMIT')
    if status != 'VALID':
        result['labels'].append('PROTOCOL_FAILURE_NO_VALID_RANKING'); return result
    need(final is not None and len(final) <= 5, 'TOP5_REQUIRED')
    selected = identities(final); need(selected <= candidates, 'OUTPUT_OUTSIDE_CANDIDATE_POOL')
    hits = gt & selected; gap = min(len(present), 5) - len(hits)
    result.update(final_hits=len(hits), gap=gap, avoidable_reranking_gap=gap,
                  pool_GT_not_in_top5=len(present - selected), hit_method_ids=sorted(hits),
                  pool_GT_not_in_top5_method_ids=sorted(present - selected))
    if gap:
        result['labels'].append('RERANKING_AVOIDABLE_GAP')
    need(len(gt) - len(hits) == result['retrieval_missed'] + result['capacity_limit'] + gap,
         'DECOMPOSITION_MISMATCH')
    return result


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    need(not OUT.exists(), 'OUTPUT_DIRECTORY_EXISTS')
    # Reuse the sealed exact-identity scorer and original strict response parser.
    sys.path.insert(0, str(LIVE))
    scorer = module('frozen_top50_scorer', LIVE / 'score.py')
    old = scorer.module('frozen_canonical_scorer', ROOT / 'experiments/class-aware-rerank-scoring-v1/score.py')
    raw(ROOT / 'experiments/class-aware-rerank-scoring-v1/score.py')
    for folder, (name, pin) in PINS.items():
        need(hashlib.sha256(raw(folder / name)).hexdigest() == pin, 'MANIFEST_PIN')
        for filename, expected in load(folder / name)['files'].items():
            path = (folder / filename).resolve()
            need(path.is_relative_to(ROOT), 'MANIFEST_PATH_ESCAPE')
            need(hashlib.sha256(raw(path)).hexdigest() == expected, 'SEALED_FILE_HASH:' + filename)
    protocol = load(COMPACT / 'protocol.json')
    cases = protocol['cases']; public = old.PUBLIC
    pub_manifest = raw(public / 'direct.manifest.json')
    need(hashlib.sha256(pub_manifest).hexdigest() == '8ced6603c56bb156072928c7969e30d22f3497bc852000e753e8c1f1e9a69f01', 'PUBLIC_PIN')
    pubfiles = json.loads(pub_manifest)['files']

    def pub(name):
        data = raw(public / name)
        need(hashlib.sha256(data).hexdigest() == pubfiles[name], 'PUBLIC_HASH:' + name)
        return json.loads(data)

    entries = {r['case_id']: r for r in pub('cases.json')['cases']}
    ca = module('frozen_source_checks', ROOT / 'experiments/class-aware-expansion-v1/class_aware_expansion_v1.py')
    raw(ROOT / 'experiments/class-aware-expansion-v1/class_aware_expansion_v1.py')
    catalogs, records, orders, conditions = {}, {}, {}, {}
    for case in cases:
        row = entries[case]; cat = pub(row['catalog'])
        need(READ[str(public / row['catalog'])] == row['catalog_sha256'], 'CATALOG_SHA')
        by_id = {r['occurrence_id']: r for r in cat['records']}
        need(len(by_id) == len(cat['records']), 'OCCURRENCE_CONFLICT')
        binding = load(HIER / 'predictions' / case / 'input-binding.json')
        need(binding['catalog_file_sha256'] == row['catalog_sha256'], 'RANKING_CATALOG_MISMATCH')
        issue = binding['public_input']; context = cat['reference']['context']
        need(all(issue[k] == context[k] for k in ('repository', 'pre_fix_sha')), 'SNAPSHOT_MISMATCH')
        need(hashlib.sha256(issue['issue_text'].encode()).hexdigest() == binding['issue_text_sha256'], 'ISSUE_HASH')
        aa = load(HIER / 'predictions' / case / 'BM25-full-order.json')['occurrence_ids']
        bb = [r['occurrence_id'] for r in load(HIER / 'predictions' / case / 'Hierarchical-full-ranking.json')['ranking']]
        need(len(aa) == len(bb) == len(by_id) and len(set(aa)) == len(aa)
             and len(set(bb)) == len(bb) and set(aa) == set(bb) == set(by_id), 'FULL_ORDER_SET')
        for arm, order in [('A', aa), ('B', bb)]:
            identities([by_id[i]['original_v2_id'] for i in order[:50]])
            preview_status = load(COMPACT / 'requests' / case / arm / 'status.json')
            request_name = 'request.json' if preview_status['status'] == 'READY' else 'request.preview.json'
            request = load(COMPACT / 'requests' / case / arm / request_name)
            if preview_status['status'] != 'READY':
                need(request['send_allowed'] is False, 'BLOCKED_PREVIEW_SEND_ALLOWED')
                request = request['request']
            payload = json.loads(request['messages'][1]['content'])
            need([r['candidate']['occurrence_id'] for r in payload['candidates']] == order[:50], 'CANDIDATE_ORDER')
            need(all(payload[k] == issue[k] for k in ('repository', 'pre_fix_sha', 'issue_text')), 'INPUT_CONDITION_MISMATCH')
            orders[case, arm] = order
        conditions[case] = dict(issue_sha256=binding['issue_text_sha256'], repository=issue['repository'],
                                pre_fix_sha=issue['pre_fix_sha'], catalog_sha256=row['catalog_sha256'])
        catalogs[case] = (cat, row, ca); records[case] = by_id
    # The previous scorer verifies the GT binding, full GT seal, exact canonical identity and source ranges.
    pilot_manifest = load(old.PILOT / 'manifest.sha256.json')
    need(READ[str(old.PILOT / 'manifest.sha256.json')] == old.PILOT_PIN, 'PILOT_PIN')
    binding_path = old.PILOT / 'private-gt-binding.json'
    binding = load(binding_path)
    need(READ[str(binding_path)] == pilot_manifest['files']['private-gt-binding.json'], 'GT_BINDING_PIN')
    old.CASES = tuple(c for c in cases if c in binding['case_ids'])
    truths, gt_audit, _ = old.truths(catalogs)
    READ.update({str(p): h for p, h in old.READ_HASHES.items()})
    canonical_truths = {}
    for case, gt in truths.items():
        ids = [records[case][i]['original_v2_id'] for i in gt]
        identities(ids)
        for mid in ids:
            need(sum(r.get('original_v2_id') == mid for r in records[case].values()) == 1, 'GT_METHOD_IDENTITY_CONFLICT')
        canonical_truths[case] = ids
    states = {(r['case_id'], r['arm']): r for r in load(LIVE / 'prediction-results.json')['statuses']}
    details, coverage = [], []
    for case in cases:
        gt = canonical_truths.get(case); pool_cover = {}
        for arm in ('A', 'B'):
            pool = [records[case][i]['original_v2_id'] for i in orders[case, arm][:50]]
            pool_cover[arm] = len(set(pool) & set(gt)) if gt is not None else None
            preview = load(COMPACT / 'requests' / case / arm / 'status.json')
            state = states.get((case, arm)); final = None
            if state is not None:
                folder = LIVE / 'predictions' / case / arm
                need(load(folder / 'status.json') == state, 'STATUS_DISAGREEMENT')
                need(raw(folder / 'request.bin') == raw(COMPACT / 'requests' / case / arm / 'request.json'), 'REQUEST_BYTES')
                status = state['status']
                if status == 'VALID':
                    parsed = scorer.a.validate_response(load(folder / 'http.json'), raw(folder / 'response.bin'),
                                load(COMPACT / 'requests' / case / arm / 'request.json'), state['request_sha256'])
                    need(parsed == state['parsed'], 'RESPONSE_PARSE_DISAGREEMENT')
                    need(set(parsed['occurrence_ranking']) == set(orders[case, arm][:50]), 'FULL_PERMUTATION')
                    final = [records[case][i]['original_v2_id'] for i in parsed['occurrence_ranking'][:5]]
            else:
                status = 'BLOCKED' if preview['status'] != 'READY' else 'NOT_EXECUTED_OUTSIDE_LIVE_SCOPE'
            result = analyze(gt, pool, final, status)
            result.update(case_id=case, arm=arm, candidate_source='BM25_TOP50' if arm == 'A' else 'HIERARCHICAL_TOP50',
                          conditions=conditions[case], offline_Top50_GT=pool_cover[arm],
                          candidate_method_ids=pool, final_top5_method_ids=final,
                          preflight_status=preview['status'], source_errors=preview.get('source_errors', []))
            details.append(result)
        coverage.append(dict(case_id=case, GT_count=len(gt) if gt is not None else None,
                             BM25_Top50_GT=pool_cover['A'], Hierarchical_Top50_GT=pool_cover['B']))
    paired_cases = [c for c in cases if c in canonical_truths and all(states.get((c, a), {}).get('status') == 'VALID' for a in ('A', 'B'))]
    aggregate = {}
    for arm in ('A', 'B'):
        chosen = [r for r in details if r['arm'] == arm and r['case_id'] in paired_cases]
        aggregate[arm] = {k: sum(r[k] for r in chosen) for k in
            ('GT_count', 'pool_GT', 'final_hits', 'theoretical_top5', 'gap', 'retrieval_missed',
             'pool_GT_not_in_top5', 'capacity_limit', 'avoidable_reranking_gap')}
        aggregate[arm]['issues'] = len(chosen)
    known = [r for r in coverage if r['GT_count'] is not None]
    totals = {k: sum(r[k] for r in known) for k in ('GT_count', 'BM25_Top50_GT', 'Hierarchical_Top50_GT')}
    totals['issues'] = len(known)
    OUT.mkdir()

    def save(name, value):
        with (OUT / name).open('x', encoding='utf-8') as f:
            f.write(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))

    a, b = aggregate['A'], aggregate['B']
    paragraph = (f"在已公开GT的回顾性案例中，{len(paired_cases)}条两组均协议有效的案例共{a['GT_count']}个GT。"
        f"BM25与Hierarchical Top-50分别覆盖{a['pool_GT']}、{b['pool_GT']}个GT，检索遗漏分别为{a['retrieval_missed']}、{b['retrieval_missed']}个。"
        f"最终Top-5各命中{a['final_hits']}、{b['final_hits']}个GT；按各Issue分别取min(池内GT,5)后求和，理论上限为{a['theoretical_top5']}、{b['theoretical_top5']}，"
        f"可改善差距为{a['gap']}、{b['gap']}。Hierarchical池内未入选的{b['pool_GT_not_in_top5']}个GT中，"
        f"{b['capacity_limit']}个属于Top-5不可避免的容量下限，不能全部归咎于模型。"
        "这表明当前检索仍遗漏大量方法，扩充候选覆盖也未自动转化为最终定位收益。"
        "结论仅限完整源码准入子集，不是独立盲测或因果证明；源码映射阻塞与未知GT案例另列N/A。")
    show = lambda v: 'N/A' if v is None else str(v)
    text = ['# Retrieval–Reranking Bottleneck Analysis v1', '', 'EXPLORATORY_POST_HOC；不运行检索或模型。', '',
        '检索遗漏＝GT−池内GT；池内未入选＝池内GT−实际命中；容量下限＝max(池内GT−5,0)；可改善重排差距＝min(池内GT,5)−实际命中。',
        '后三者中，池内未入选包含容量限制，不能再次相加。数量分解不指定具体哪个遗漏方法“属于容量”。', '',
        '## 完整离线排名的候选覆盖', '', '|案例|GT|BM25 Top-50|Hierarchical Top-50|', '|---|---:|---:|---:|']
    text += ['|' + '|'.join(show(r[k]) for k in ('case_id', 'GT_count', 'BM25_Top50_GT', 'Hierarchical_Top50_GT')) + '|' for r in coverage]
    text += ['', f"可靠GT全{totals['issues']}例：BM25 {totals['BM25_Top50_GT']}/{totals['GT_count']}；Hierarchical {totals['Hierarchical_Top50_GT']}/{totals['GT_count']}。", '',
        '## 真实LLM执行分组', '', '|案例|组|状态|GT|实际入池GT|Top-5命中|理论上限|差距|检索遗漏|池内未选|容量下限|',
        '|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    text += ['|' + '|'.join(show(r[k]) for k in ('case_id', 'arm', 'status', 'GT_count', 'pool_GT', 'final_hits',
              'theoretical_top5', 'gap', 'retrieval_missed', 'pool_GT_not_in_top5', 'capacity_limit')) + '|' for r in details]
    text += ['', 'A＝BM25 Top-50；B＝Hierarchical Top-50。BLOCKED的预览候选不冒充实际入池；协议失败不产生有效Top-5。', '',
        '## 三项发现', '',
        f"1. 实际配对{len(paired_cases)}例：A覆盖{a['pool_GT']}/{a['GT_count']}、B覆盖{b['pool_GT']}/{b['GT_count']}，实际Top-5均{a['final_hits']}/{a['GT_count']}。",
        f"2. A检索遗漏{a['retrieval_missed']}、可改善重排差距{a['gap']}、容量下限{a['capacity_limit']}；B分别{b['retrieval_missed']}、{b['gap']}、{b['capacity_limit']}。",
        '3. 另两条可靠GT案例因源码映射BLOCKED；三条未知GT均保留，不能扩充模型评价分母。', '',
        '## 可用于技术报告第6章的结论', '', paragraph, '',
        '## 复核与边界', '', '完整method_id保留类和签名，先复用旧评分器精确GT/source绑定，再核对冻结occurrence映射；不使用名称模糊匹配。',
        '原输入、快照、候选集合与状态逐组保存在details.json，源文件路径与SHA-256在source-hashes.json。',
        '不同Issue不被视为相同快照；只有各自A/B的Issue、快照和格式一致时才进入配对汇总。所有原实验保持不变。', '']
    save('analysis.md', '\n'.join(text)); save('chapter6-conclusion.md', paragraph + '\n')
    save('details.json', details)
    save('summary.json', dict(classification='EXPLORATORY_POST_HOC', offline_known_GT=totals,
        paired_cases=paired_cases, paired_aggregate=aggregate, coverage=coverage,
        GT_mapping_count=len(gt_audit), new_API_calls=0, retrieval_reexecution=False))
    raw(Path(__file__)); raw(ROOT / 'tests/test_retrieval_reranking_bottleneck.py')
    save('source-hashes.json', {str(Path(p).relative_to(ROOT)): h for p, h in sorted(READ.items())})
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.iterdir() if p.is_file()}
    save('manifest.sha256.json', dict(files=files))
    pin = hashlib.sha256((OUT / 'manifest.sha256.json').read_bytes()).hexdigest()
    save('seal.json', dict(manifest_sha256=pin))
    print(json.dumps(dict(offline=totals, paired=aggregate, seal=pin), ensure_ascii=False))


if __name__ == '__main__':
    main()

