# src/evidence_replay.py
"""
IssueLens — Evidence Replay
分步展示 Issue-to-Code 定位链路，用于 Demo 视频与答辩现场演示。

用法：
  python src\evidence_replay.py --benchmark data\benchmark.json --id petclinic-pet-duplicate-name
  python src\evidence_replay.py --benchmark data\benchmark.json --id petclinic-pet-duplicate-name --auto 2
  python src\evidence_replay.py --benchmark data\benchmark.json --id petclinic-pet-duplicate-name --no-llm
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from issue_input import add_arguments, direct_case, record_input  # noqa: E402

from retriever import (  # noqa: E402
    load_json,
    iter_methods,
    extract_keywords,
    score_method,
    format_candidate,
    method_key,
    CN_EN_TECH_TERMS,
)
from llm_reranker import (  # noqa: E402
    load_index_for_repo,
    rerank_candidates,
)


BAR = "─" * 70
DBL = "═" * 70


def banner(case):
    print(DBL)
    print(" IssueLens — Evidence Replay")
    print(DBL)
    print(f" Case ID  : {case.get('id')}")
    print(f" Repo     : {case.get('repo_path')}")
    print()
    print(" Issue:")
    issue = (case.get("issue_text") or "").strip()
    for ln in issue.splitlines():
        print(f"   {ln}")
    print()


def pause(delay, next_step):
    if delay is None:
        try:
            input(f"  [按回车继续 → {next_step}] ")
        except EOFError:
            pass
    else:
        time.sleep(delay)


def step_1_index(case, methods):
    print(BAR)
    print(" Step 1/5  Index — JavaParser 结构索引")
    print(BAR)

    files = set()
    classes = set()
    for m in methods:
        f = m.get("file") or m.get("path")
        if f:
            files.add(f)
        c = m.get("class") or m.get("class_name")
        if c:
            classes.add(c)

    print(f" Repo      : {case.get('repo_path')}")
    print(f" Files     : {len(files)} java files scanned")
    print(f" Classes   : {len(classes)} classes")
    print(f" Methods   : {len(methods)} methods indexed")
    print()
    print(" 每个方法提取：类名 / 包名 / 方法名 / 行号 / Spring 注解")
    print("              字符串常量 / 方法调用 / 参数列表")
    print()


def step_2_keywords(case):
    print(BAR)
    print(" Step 2/5  Keywords — 关键词提取（英文 token + jieba + 术语映射）")
    print(BAR)

    issue = case["issue_text"]
    import re
    import jieba

    en_tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", issue.lower())
    zh_tokens = []
    for tok in jieba.cut(issue):
        tok = tok.strip().lower()
        if len(tok) < 2:
            continue
        if not re.search(r"[\u4e00-\u9fff]", tok):
            continue
        zh_tokens.append(tok)

    # 术语映射命中
    mapped = [(t, CN_EN_TECH_TERMS[t]) for t in set(zh_tokens) if t in CN_EN_TECH_TERMS]

    keywords = extract_keywords(issue)

    print(f" EN tokens : {en_tokens[:12]}{' ...' if len(en_tokens) > 12 else ''}")
    print(f" ZH tokens : {zh_tokens[:12]}{' ...' if len(zh_tokens) > 12 else ''}")
    if mapped:
        print(f" Term map  :")
        for zh, ens in mapped[:8]:
            print(f"             {zh} → {', '.join(ens)}")
    print(f" Combined  : {len(keywords)} unique keywords")
    print()


def step_3_rule(case, methods, topk):
    print(BAR)
    print(" Step 3/5  Rule Retrieval — 6 信号打分，Top-%d" % topk)
    print(BAR)

    keywords = extract_keywords(case["issue_text"])
    scored = []
    for m in methods:
        s, d = score_method(m, keywords, case["issue_text"])
        scored.append({"method": m, "score": s, "detail": d})
    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:topk]

    print(" 信号权重：方法名×12 + 字符串×6 + 签名×3 + 注解×3")
    print("           + 调用×2 + 文件路径×1 + 错误覆盖率最高 20 + 异常抛出 8")
    print()

    for i, item in enumerate(top, 1):
        print(format_candidate(i, item))
        print(BAR)

    return top


def step_4_rerank(case, top, provider, dry_run, no_llm):
    print(BAR)
    print(" Step 4/5  LLM Rerank — 分层语义重排")
    print(BAR)

    if no_llm:
        print(" [--no-llm] 跳过 LLM 重排，仅展示规则层结果")
        print()
        return top, None

    reordered, info = rerank_candidates(
        case["issue_text"], top,
        provider=provider, dry_run=dry_run,
    )

    fallback = not info["reordered"] and not dry_run
    rank_from_rule = {method_key(c["method"]): r for r, c in enumerate(top, 1)}

    print(f" Provider : {provider}")
    if dry_run:
        print(" Mode     : dry-run（反转顺序，仅测排版）")
    if fallback:
        print(f" Mode     : [fallback: rule-only]  reason: {info['reason']}")
    print()

    for i, item in enumerate(reordered, 1):
        block = format_candidate(i, item)
        if not fallback:
            key = method_key(item["method"])
            rule_rank = rank_from_rule.get(key)
            if rule_rank is not None:
                if rule_rank == i:
                    arrow = f"       rank: rule #{rule_rank} (unchanged)"
                else:
                    arrow = f"       rank: rule #{rule_rank} → LLM #{i}"
                lines = block.split("\n")
                insert_at = next(
                    (idx + 1 for idx, ln in enumerate(lines) if ln.strip().startswith("role:")),
                    2,
                )
                lines.insert(insert_at, arrow)
                block = "\n".join(lines)
        print(block)
        print(BAR)

    if not fallback and info.get("reason"):
        print(" LLM Rationale:")
        for ln in info["reason"].strip().splitlines():
            print(f"   {ln}")
        print(BAR)

    return reordered, info


def step_5_verify(case):
    print(BAR)
    print(" Step 5/5  Verification — 可运行验证")
    print(BAR)

    cmd = case.get("test_command")
    if not cmd:
        print(" [无 test_command]")
        print()
        return

    print(f" Command  : {cmd}")
    print()
    print(" 验证逻辑：")
    print("   1. 执行 mvn test，解析 Surefire XML")
    print("   2. 记录 before（修复前）红/绿状态")
    print("   3. 应用修改后，记录 after 状态")
    print("   4. 输出红→绿对照，证明定位指向真实修复点")
    print()


def show_local_evidence(case, top, index_path, index_sha256, metadata_sha256, output=None):
    """Explicit v2 display only; never changes retrieval or legacy certification."""
    from evidence_validator import validate_evidence_v2, _write_report
    selected = next((e['evidence_id'] for item in top
                     for e in item['method'].get('evidence_occurrences', [])), None)
    if selected is None:
        print(' Local evidence: UNVERIFIABLE (no indexed evidence occurrences)')
        print(' Repository evidence: NOT_CHECKED')
        return None
    report = validate_evidence_v2(case['repo_path'], index_path, selected,
                                  index_sha256=index_sha256, metadata_sha256=metadata_sha256)
    print(BAR)
    print(' Evidence certification: evidence-local-v2')
    print(' Scope: PINNED_INDEX_SOURCE_BYTE_CORRESPONDENCE; caller-trusted input hashes')
    print(' Source-byte certification only; NOT semantic correctness or root-cause proof.')
    print(' Repository evidence:', report['overall_verdict'])
    print(' Repository blockers:', report['summary']['blocking_issues'])
    for rank, item in enumerate(top, 1):
        method = item['method']
        mid = method.get('method_id')
        path = method.get('file') or method.get('path')
        expected = method.get('evidence_occurrences', [])
        rows = [r for r in report['evidence_results'] if r['method_id']==mid and r['file_path']==path]
        ids = [e['evidence_id'] for e in expected]
        actual = [r['evidence_id'] for r in rows]
        bound = bool(ids) and len(ids)==len(set(ids)) and sorted(ids)==sorted(actual)
        passed = sum(r['status']=='VERIFIED' for r in rows) if bound else 0
        verdict = 'PASS' if bound and passed==len(ids) else 'UNVERIFIABLE'
        print(f' #{rank} {mid}')
        print(f' Local evidence: {verdict} ({passed}/{len(ids)} VERIFIED)')
        if bound:
            for row in rows:
                state = 'PASS' if row['status']=='VERIFIED' else row['status']
                print(f"   {state} {row['evidence_id']} {row['reason_code']}")
                if row['status']=='VERIFIED':
                    print('    Source:', repr(row['basis']['extracted_source_snippet']))
    print(' Runtime tests: NOT_RUN by evidence certification')
    if output:
        _write_report(output, report, case['repo_path'], [index_path, str(index_path)+'.meta.json'])
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", default="data/benchmark.json")
    parser.add_argument("--id", default=None)
    parser.add_argument("--topk", type=int, default=5)
    parser.add_argument("--provider", default="deepseek",
                        choices=["deepseek", "qwen"])
    parser.add_argument("--dry-run", action="store_true",
                        help="LLM 干跑（反转顺序）")
    parser.add_argument("--no-llm", action="store_true",
                        help="跳过 Step 4，仅展示规则层")
    parser.add_argument("--auto", type=float, default=None,
                        help="每步间隔秒数（默认回车继续）")
    parser.add_argument("--index", help="显式复用已有索引，不复制或重建")
    parser.add_argument("--evidence-protocol", choices=["evidence-local-v2"])
    parser.add_argument("--evidence-index-sha256")
    parser.add_argument("--evidence-metadata-sha256")
    parser.add_argument("--evidence-out", help="独立认证报告路径，拒绝覆盖")
    add_arguments(parser)
    args = parser.parse_args()
    if args.evidence_protocol and (not args.no_llm or not args.evidence_index_sha256 or not args.evidence_metadata_sha256):
        parser.error('evidence-local-v2 requires --no-llm and both pinned evidence hashes')
    if not args.evidence_protocol and any((args.evidence_index_sha256,args.evidence_metadata_sha256,args.evidence_out)):
        parser.error('Evidence options require explicit --evidence-protocol evidence-local-v2')

    case = direct_case(args, parser)
    if case is None:
        bench = load_json(args.benchmark)
        if isinstance(bench, dict):
            cases = bench.get("cases") or [bench]
        else:
            cases = bench
        if args.id:
            case = next((c for c in cases if c.get("id") == args.id), None)
            if not case:
                raise SystemExit(f"找不到 case: {args.id}")
        else:
            case = cases[0]
    case = record_input(case, args, parser)

    index_path = args.index or str(Path(case["repo_path"]) / "index.json")
    methods = list(iter_methods(load_json(index_path)))
    if not methods:
        raise SystemExit("index.json 里没解析到方法")

    banner(case)

    step_1_index(case, methods)
    pause(args.auto, "Step 2/5 Keywords")

    step_2_keywords(case)
    pause(args.auto, "Step 3/5 Rule Retrieval")

    top = step_3_rule(case, methods, args.topk)
    pause(args.auto, "Step 4/5 LLM Rerank")

    step_4_rerank(case, top, args.provider, args.dry_run, args.no_llm)
    pause(args.auto, "Step 5/5 Verification")

    step_5_verify(case)
    if args.evidence_protocol:
        show_local_evidence(case, top, index_path, args.evidence_index_sha256,
                            args.evidence_metadata_sha256, args.evidence_out)

    print(DBL)
    print(" Evidence Replay 完成")
    print(DBL)


if __name__ == "__main__":
    main()
