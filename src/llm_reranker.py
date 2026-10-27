# src/llm_reranker.py
import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from retriever import (  # noqa: E402
    load_json,
    iter_methods,
    extract_keywords,
    score_method,
    render_case_block,
    method_key,
)


PROVIDERS = {
    "deepseek": {
        "url": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-chat",
        "env_key": "DEEPSEEK_API_KEY",
    },
    "qwen": {
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "model": "qwen-plus",
        "env_key": "DASHSCOPE_API_KEY",
    },
}


SYSTEM_PROMPT = """你是 Java 后端代码定位专家。给定一个 GitHub Issue 和若干候选方法，请从"最可能是实际修复点"的角度重新排序。

判断视角：
1. HTTP 入口优先：带 @Controller / @GetMapping / @PostMapping / @RequestMapping 的方法通常是用户请求入口
2. 业务实现层优先：@Service / @Repository 标记的方法，而不是接口声明或无方法体的方法
3. 语义匹配度：方法名、字符串常量、调用关系与 Issue 描述的匹配程度
4. 辅助方法降权：private 方法、名字类似 isXxx / validateXxx / buildXxx 的通常是内部辅助方法，不是请求入口
5. 同名方法：如果同名方法出现在多个类里，优先选择 @Controller 或 @Service 标记的那个

只输出 JSON，不要输出任何解释文字。格式：
{"ranking": [原编号的排列], "reason": "一句话理由"}

例如候选有 5 个，#1 #2 #3 #4 #5，如果认为 #3 最可能，其次 #1，然后 #5 #2 #4，则输出：
{"ranking": [3, 1, 5, 2, 4], "reason": "..."}

不要生成补丁。不要修改代码。只做排序。"""


def _build_user_prompt(issue_text: str, candidates: list) -> str:
    lines = ["[Issue]", issue_text.strip(), "", "[候选方法]"]
    for i, c in enumerate(candidates, 1):
        m = c["method"]
        name = m.get("name") or "?"
        cls = m.get("class") or m.get("class_name") or "?"
        file = m.get("file") or m.get("path") or "?"
        annotations = m.get("annotations") or []
        strings = m.get("strings") or []
        calls = m.get("calls") or []
        score = c.get("score", 0.0)

        lines.append(f"#{i} {cls}#{name}  score={score:.2f}")
        lines.append(f"   文件: {file}")
        if annotations:
            lines.append(f"   注解: {annotations}")
        if strings:
            lines.append(f"   字符串: {strings[:5]}")
        if calls:
            lines.append(f"   调用: {calls[:8]}")
        lines.append("")
    return "\n".join(lines)


def _extract_json(text: str):
    if not text:
        return None
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = text.replace("```", "")
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def _validate_ranking(ranking, n: int) -> bool:
    if not isinstance(ranking, list):
        return False
    if len(ranking) != n:
        return False
    # JSON booleans/floats are not positional IDs; reject before sort/indexing.
    if any(type(index) is not int for index in ranking):
        return False
    if sorted(ranking) != list(range(1, n + 1)):
        return False
    return True


def _call_llm(provider: str, system: str, user: str, timeout: int = 60) -> str:
    cfg = PROVIDERS[provider]
    api_key = os.environ.get(cfg["env_key"])
    if not api_key:
        raise RuntimeError(f"未设置环境变量 {cfg['env_key']}")

    body = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        cfg["url"],
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    obj = json.loads(raw)
    return obj["choices"][0]["message"]["content"]


def rerank_candidates(issue_text, candidates, *, provider="deepseek",
                      dry_run=False, timeout=60, verbose=False):
    n = len(candidates)
    info = {"provider": provider, "dry_run": dry_run, "reordered": False, "reason": ""}

    if n <= 1:
        info["reason"] = "候选数 <= 1，跳过重排"
        return candidates, info

    user_prompt = _build_user_prompt(issue_text, candidates)

    if dry_run:
        ranking = list(range(n, 0, -1))
        info["reason"] = "[dry-run] 反转顺序"
    else:
        try:
            content = _call_llm(provider, SYSTEM_PROMPT, user_prompt, timeout=timeout)
        except Exception as e:
            info["reason"] = f"LLM 调用失败: {e}"
            return candidates, info

        if verbose:
            print(f"[llm] raw output:\n{content}\n")

        obj = _extract_json(content)
        if not obj or "ranking" not in obj:
            info["reason"] = f"JSON 解析失败: {content[:200]}"
            return candidates, info

        ranking = obj["ranking"]
        info["reason"] = obj.get("reason", "")

    if not _validate_ranking(ranking, n):
        info["reason"] = f"ranking 不合法: {ranking}"
        return candidates, info

    reordered = [candidates[i - 1] for i in ranking]
    info["reordered"] = True
    return reordered, info


def load_index_for_repo(repo_path, cache):
    if repo_path in cache:
        return cache[repo_path]
    index_path = Path(repo_path) / "index.json"
    if not index_path.exists():
        raise SystemExit(f"[llm_reranker] 找不到 {index_path}")
    obj = load_json(str(index_path))
    methods = list(iter_methods(obj))
    cache[repo_path] = methods
    return methods


def rank_with_retriever(case, methods, topk=5):
    keywords = extract_keywords(case["issue_text"])
    scored = []
    for m in methods:
        s, d = score_method(m, keywords, case["issue_text"])
        scored.append({"method": m, "score": s, "detail": d})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:topk]


def _compute_metrics(case, scored, topk):
    gt_methods = [m.lower() for m in case.get("ground_truth_methods", [])]
    gt_files = [f.lower() for f in case.get("ground_truth_files", [])]

    top1_method = (scored[0]["method"].get("name") or "").lower()
    top1_file = (scored[0]["method"].get("file") or "").lower()
    file_hit = any(g in top1_file for g in gt_files)
    method_hit = top1_method in gt_methods

    method_rank = None
    for r, item in enumerate(scored, 1):
        if (item["method"].get("name") or "").lower() in gt_methods:
            method_rank = r
            break

    file_rank = None
    for r, item in enumerate(scored, 1):
        f = (item["method"].get("file") or "").lower()
        if any(g in f for g in gt_files):
            file_rank = r
            break

    return {
        "file_top1": file_hit,
        "method_top1": method_hit,
        "method_rank": method_rank,
        "file_rank": file_rank,
        "method_topk": method_rank is not None and method_rank <= topk,
    }


def evaluate_case(case, methods, topk=5, provider="deepseek",
                  dry_run=False, verbose=False):
    top = rank_with_retriever(case, methods, topk=topk)
    if not top:
        return None

    before = _compute_metrics(case, top, topk)

    reordered, info = rerank_candidates(
        case["issue_text"], top,
        provider=provider, dry_run=dry_run, verbose=verbose,
    )
    after = _compute_metrics(case, reordered, topk)

    rank_from_rule = {}
    for r, item in enumerate(top, 1):
        rank_from_rule[method_key(item["method"])] = r

    return {
        "id": case.get("id"),
        "case": case,
        "keywords": extract_keywords(case["issue_text"]),
        "methods_count": len(methods),
        "before_scored": top,
        "after_scored": reordered,
        "rank_from_rule": rank_from_rule,
        "before": before,
        "after": after,
        "info": info,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", default="data/benchmark.json")
    parser.add_argument("--id", default=None)
    parser.add_argument("--topk", type=int, default=5)
    parser.add_argument("--provider", default="deepseek", choices=list(PROVIDERS.keys()))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    cases = load_json(args.benchmark)
    if isinstance(cases, dict):
        cases = cases.get("cases") or [cases]

    if args.id:
        cases = [c for c in cases if c.get("id") == args.id]
        if not cases:
            raise SystemExit(f"找不到 case: {args.id}")

    print(f"评测 case 数: {len(cases)}")
    print(f"provider: {args.provider}  dry_run: {args.dry_run}")
    print("=" * 100)

    cache = {}
    results = []

    for case in cases:
        methods = load_index_for_repo(case["repo_path"], cache)
        r = evaluate_case(
            case, methods, topk=args.topk,
            provider=args.provider, dry_run=args.dry_run, verbose=args.verbose,
        )
        if r is None:
            continue
        results.append(r)

        fallback = (not r["info"]["reordered"]) and (not args.dry_run)

        extra = {
            "methods_count": r["methods_count"],
            "keywords": r["keywords"],
            "provider": args.provider,
            "dry_run": args.dry_run,
            "fallback": fallback,
            "reason": r["info"].get("reason", ""),
            "rank_from_rule": r["rank_from_rule"],
        }

        print(render_case_block(
            r["case"], r["after_scored"], args.topk,
            mode="rerank", extra=extra,
        ))

        b, a = r["before"], r["after"]
        print(f" [metrics] before: fileTop1={b['file_top1']} methodTop1={b['method_top1']} methodRank={b['method_rank']}")
        print(f" [metrics] after : fileTop1={a['file_top1']} methodTop1={a['method_top1']} methodRank={a['method_rank']}")
        print()

    n = len(results)
    if n == 0:
        return

    print("\n汇总")
    print("=" * 100)
    print(f"重排前 文件级 Top-1: {sum(1 for r in results if r['before']['file_top1'])}/{n}")
    print(f"重排后 文件级 Top-1: {sum(1 for r in results if r['after']['file_top1'])}/{n}")
    print(f"重排前 方法级 Top-1: {sum(1 for r in results if r['before']['method_top1'])}/{n}")
    print(f"重排后 方法级 Top-1: {sum(1 for r in results if r['after']['method_top1'])}/{n}")
    print(f"重排前 方法级 Top-{args.topk}: {sum(1 for r in results if r['before']['method_topk'])}/{n}")
    print(f"重排后 方法级 Top-{args.topk}: {sum(1 for r in results if r['after']['method_topk'])}/{n}")


if __name__ == "__main__":
    main()
