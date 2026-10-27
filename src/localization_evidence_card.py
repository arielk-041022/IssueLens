"""Read-only Markdown view of saved rankings and Evidence Trace; no localization."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re

from evidence_trace_manifest import trace, final_rows, sha, require
from evidence_validator import strict_json

ROLES = {'ranking', 'trace', 'index', 'metadata', 'report', 'snapshot', 'context'}
IDENTITY = ('repo_id', 'source_snapshot_id', 'file_path', 'method_id')
MISSING = '未提供'


def literal(value):
    if value is None or value == '' or value == []:
        return MISSING
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    # Saved code/reasons are data, never Markdown directives or executable HTML.
    fence = '`' * max(1, max((len(x) for x in re.findall(r'`+', text)), default=0) + 1)
    return fence + ' ' + text.replace('\n', ' ↵ ').replace('\r', '') + ' ' + fence


def render_card(issue_id, ranking_kind, docs, hashes):
    """Reproduce the existing trace engine and require exact saved-record equality."""
    require(ranking_kind in ('historical-bm25', 'saved-rule', 'saved-llm'), 'RANKING_KIND_REQUIRED')
    required = {'ranking', 'index', 'metadata', 'validator-report'}
    require(required <= set(hashes), 'MISSING_INPUT_FINGERPRINT')
    original_hashes = docs['trace'].get('input_sha256', {})
    require(required <= set(original_hashes) and all(hashes.get(k) == v for k, v in original_hashes.items()),
            'SAVED_TRACE_INPUT_HASH_MISMATCH')
    reproduced = trace(docs['ranking'], docs['index'], docs['metadata'], docs['report'],
                       docs['snapshot'], original_hashes)
    require(reproduced == docs['trace'], 'SAVED_TRACE_DISAGREEMENT')
    rows = final_rows(docs['ranking'])
    case = docs['context']
    heading = {'historical-bm25': '历史BM25候选，非最终LLM结果',
               'saved-rule': '保存的Rule候选', 'saved-llm': '保存的LLM排序（不等于证据已核验）'}[ranking_kind]
    lines = [f'# {issue_id} 定位证据卡', '', heading, '',
             '- Issue原文（换行可视化为↵，输入文件不变）：' + literal(case.get('issue_text')),
             '- Issue URL：' + literal(case.get('issue_url')),
             '- 保存的整仓Validator结论：' + literal(reproduced['validator_original_overall_verdict']),
             '- 保存的选择证据局部结果：' + literal(reproduced['validator_original_selected_evidence']),
             '- 保存的测试状态：' + literal(reproduced['runtime_tests']),
             '', '只重现保存报告，不重新认证当前源码；局部PASS不等于整仓PASS，'
             '源码来源匹配不等于Issue相关性、AST语义或Bug根因认证。', '',
             '## 整仓未排除的问题', '']
    codes = Counter(i.get('reason_code', MISSING) for i in docs['report'].get('issues', []))
    lines.append('原报告问题代码及记录数：' + literal(dict(sorted(codes.items())) if codes else None))
    lines.append('这些是整仓记录，不自动归因于下面每个候选。')
    index = {}
    for file in docs['index']:
        for cls in file['classes']:
            for method in cls['methods']:
                index.setdefault((file['file'], method.get('method_id')), []).append(method)
    for row, item in zip(rows, reproduced['candidates']):
        identity = item['method_identity']
        require(all(identity[k] == row.get(k) for k in IDENTITY), 'CANDIDATE_IDENTITY_MISMATCH')
        span = row.get('source_line_range')
        if item['trace_status'] == 'BOUND':
            methods = index.get((identity['file_path'], identity['method_id']), [])
            require(len(methods) == 1, 'METHOD_NOT_UNIQUE')
            method = methods[0]
            indexed = [method.get('start_line'), method.get('end_line')]
            require(span is None or span == indexed, 'SAVED_CANDIDATE_RANGE_MISMATCH')
            span = indexed
        lines += ['', f"## 候选 {item['final_rank']}", '',
                  '- 原始排名：' + literal(item['original_rank']),
                  '- 完整方法身份：' + literal(identity['method_id']),
                  '- 身份协议：' + literal(item['method_identity_protocol']),
                  '- 仓库：' + literal(identity['repo_id']),
                  '- 快照标签：' + literal(identity['source_snapshot_id']),
                  '- Git提交：' + literal(item['snapshot'].get('commit')),
                  '- 源码路径：' + literal(identity['file_path']),
                  '- 方法行号（保存记录，起止均含）：' + literal(span),
                  '- 候选关联状态：' + literal(item['trace_status']),
                  '- 保存的源码事实状态：' + literal(item['source_fact_status']),
                  '- 核验作用范围：' + literal(item['verification_scope'].get('certification')),
                  '', '### 支持性源码记录', '',
                  '这里的“支持”仅指保存的源码片段存在性；不生成推荐理由。']
        support = [e for e in item['evidence'] if e['status'] != 'SOURCE_FACT_INCONSISTENCY_RECORDED']
        negative = [e for e in item['evidence'] if e['status'] == 'SOURCE_FACT_INCONSISTENCY_RECORDED']
        def evidence(ev):
            saved = ev.get('validator_result') or {}
            lines.extend(['', '- evidence_id：' + literal(ev.get('evidence_id')),
                          '  - 类型：' + literal(ev.get('evidence_type')),
                          '  - 源码范围（1-based UTF-16，起止均含）：' + literal(ev.get('source_range')),
                          '  - 原始片段：' + literal(saved.get('basis', {}).get('extracted_source_snippet')),
                          '  - Trace状态：' + literal(ev.get('status')),
                          '  - Validator原状态及错误码：' + literal(saved.get('status')) + ' / ' + literal(saved.get('reason_code')),
                          '  - 未核验原因：' + literal(ev.get('unverified_reasons'))])
        for ev in support:
            evidence(ev)
        if not support:
            lines.append('未提供；UNVERIFIABLE。未绑定候选保持UNBOUND。')
        lines += ['', '### 保存的反面证据与未排除疑点', '']
        for ev in negative:
            evidence(ev)
        if not negative:
            lines.append('反面源码证据：未提供；不表示不存在反证。')
        lines.append('原Trace未核验原因：' + literal(item['unverified_reasons']))
        lines.append('未排除：源码事实不能独立证明该候选与Issue相关；整仓原结论保持不变。')
        reason = docs['ranking'].get('reason') if ranking_kind == 'saved-llm' and isinstance(docs['ranking'], dict) else None
        lines += ['', '### 保存的模型推断', '',
                  '模型理由（原排序级推断，未核验，不逐候选重新分配）：' + literal(reason),
                  '', '### 测试命令及执行状态', '',
                  '- 保存的测试命令：' + literal(case.get('test_command')),
                  '- 保存的命令执行状态：' + literal(case.get('test_execution_status')),
                  '- 本次展示：未执行测试；打印命令不构成已执行证明。']
    lines += ['', '## 输入指纹', '']
    lines += [f'- {name} SHA-256：{literal(value)}' for name, value in sorted(hashes.items())]
    return '\n'.join(lines) + '\n'


def read_bundle(path, expected):
    """Small presentation-only binding file. All input roles are explicit and pinned."""
    require(not any('private' in p.lower() or 'scoring-private' in p.lower() for p in path.resolve().parts), 'PRIVATE_INPUT_FORBIDDEN')
    content = path.read_bytes()
    require(sha(content) == expected, 'BUNDLE_SHA256_MISMATCH')
    bundle = strict_json(content)
    require(set(bundle['files']) == ROLES, 'UNEXPECTED_INPUT_ROLE')
    docs, hashes = {}, {}
    for role, ref in bundle['files'].items():
        source = (path.parent / ref['path']).resolve()
        require(not any('private' in p.lower() or 'scoring-private' in p.lower() for p in source.parts), 'PRIVATE_INPUT_FORBIDDEN')
        content = source.read_bytes()
        require(sha(content) == ref['sha256'], 'INPUT_SHA256_MISMATCH:' + role)
        docs[role] = strict_json(content)
        hashes['validator-report' if role == 'report' else role] = sha(content)
    contexts = [c for c in docs['context'].get('cases', []) if c.get('id') == bundle['context_case_id']]
    require(len(contexts) == 1, 'CONTEXT_NOT_UNIQUE')
    docs['context'] = contexts[0]
    return bundle, docs, hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--bundle-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        require(not args.output.exists(), 'OUTPUT_EXISTS_NO_OVERWRITE')
        bundle, docs, hashes = read_bundle(args.bundle, args.bundle_sha256)
        text = render_card(bundle['issue_id'], bundle['ranking_kind'], docs, hashes)
        with args.output.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(text); stream.flush(); os.fsync(stream.fileno())
        print('EVIDENCE_CARD_SAVED', args.output, 'SHA256', sha(args.output.read_bytes()))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, 'EVIDENCE_CARD_ERROR: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
