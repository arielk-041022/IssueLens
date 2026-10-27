# src/metrics.py
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from retriever import (  # noqa: E402
    load_json,
    iter_methods,
    extract_keywords,
    score_method,
)


def load_index_for_repo(repo_path: str, index_cache: dict):
    if repo_path in index_cache:
        return index_cache[repo_path]

    index_path = Path(repo_path) / "index.json"
    if not index_path.exists():
        raise SystemExit(f"[metrics] 找不到 {index_path}，请先对 {repo_path} 运行 java-indexer")

    obj = load_json(str(index_path))
    methods = list(iter_methods(obj))
    index_cache[repo_path] = methods
    print(f"[metrics] 加载 {repo_path} 方法数: {len(methods)}")
    return methods


def evaluate_case(case, methods, topk=5, disable=None):
    disable = disable or set()
    keywords = extract_keywords(case["issue_text"])

    scored = []
    for m in methods:
        s, d = score_method(m, keywords, case["issue_text"], disable=disable)
        scored.append({"method": m, "score": s, "detail": d})
    scored.sort(key=lambda x: x["score"], reverse=True)

    gt_methods = [m.lower() for m in case.get("ground_truth_methods", [])]
    gt_files = [f.lower() for f in case.get("ground_truth_files", [])]

    top1_method = (scored[0]["method"].get("name") or "").lower()
    top1_file = (scored[0]["method"].get("file") or "").lower()

    file_top1_hit = any(g in top1_file for g in gt_files)
    method_top1_hit = top1_method in gt_methods

    method_rank = None
    for rank, item in enumerate(scored, 1):
        name = (item["method"].get("name") or "").lower()
        if name in gt_methods:
            method_rank = rank
            break

    file_rank = None
    for rank, item in enumerate(scored, 1):
        file = (item["method"].get("file") or "").lower()
        if any(g in file for g in gt_files):
            file_rank = rank
            break

    return {
        "id": case.get("id"),
        "gt_methods": case.get("ground_truth_methods"),
        "gt_files": case.get("ground_truth_files"),
        "file_rank": file_rank,
        "method_rank": method_rank,
        "file_top1_hit": file_top1_hit,
        "method_top1_hit": method_top1_hit,
        "method_top5_hit": method_rank is not None and method_rank <= topk,
        "top1_method": scored[0]["method"].get("name"),
        "top1_file": scored[0]["method"].get("file"),
        "top1_score": scored[0]["score"],
        "top2_method": scored[1]["method"].get("name") if len(scored) > 1 else None,
        "top2_score": scored[1]["score"] if len(scored) > 1 else None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", default="data/benchmark.json")
    parser.add_argument("--topk", type=int, default=5)
    parser.add_argument("--disable", default="")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    disable = set(x.strip() for x in args.disable.split(",") if x.strip())

    cases = load_json(args.benchmark)
    if isinstance(cases, dict):
        cases = cases.get("cases") or [cases]

    if not args.quiet:
        print(f"评测 case 数: {len(cases)}")
        if disable:
            print(f"消融禁用项: {sorted(disable)}")
        print("=" * 100)

    index_cache = {}
    results = []
    for case in cases:
        methods = load_index_for_repo(case["repo_path"], index_cache)
        r = evaluate_case(case, methods, topk=args.topk, disable=disable)
        results.append(r)

        if not args.quiet:
            print(f"[{r['id']}]")
            print(f"  GT 方法:  {r['gt_methods']}")
            print(f"  Top-1 方法: {r['top1_method']}  (score={r['top1_score']:.2f})")
            print(f"  Top-2 方法: {r['top2_method']}  (score={r['top2_score']:.2f})")
            print(f"  文件级排名: {r['file_rank']}   方法级排名: {r['method_rank']}")
            print(f"  文件级 Top-1: {'OK' if r['file_top1_hit'] else 'X'}   "
                  f"方法级 Top-1: {'OK' if r['method_top1_hit'] else 'X'}   "
                  f"方法级 Top-{args.topk}: {'OK' if r['method_top5_hit'] else 'X'}")
            print("-" * 100)

    n = len(results)
    file_top1 = sum(1 for r in results if r["file_top1_hit"])
    method_top1 = sum(1 for r in results if r["method_top1_hit"])
    method_topk = sum(1 for r in results if r["method_top5_hit"])

    print("\n汇总指标")
    print("=" * 100)
    print(f"文件级 Top-1 命中率:  {file_top1}/{n} = {file_top1/n:.1%}")
    print(f"方法级 Top-1 命中率:  {method_top1}/{n} = {method_top1/n:.1%}")
    print(f"方法级 Top-{args.topk} 命中率:  {method_topk}/{n} = {method_topk/n:.1%}")


if __name__ == "__main__":
    main()