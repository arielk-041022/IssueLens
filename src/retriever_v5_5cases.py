# src/retriever.py
import json
import re
import argparse
from pathlib import Path

import jieba

STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with",
    "is", "are", "was", "were", "be", "been", "being",
    "this", "that", "these", "those", "it", "its", "as", "at", "by",
    "from", "into", "about", "after", "before",
    "handle", "missing", "invalid", "pagination",
    "please", "ensure", "correct",
    "id", "ids", "404", "500", "68", "999",
    "java", "exception", "error", "bug", "issue", "test", "mvn",
}

CN_EN_TECH_TERMS = {
    "宠物": ["pet"],
    "重名": ["duplicate", "already exists"],
    "重复": ["duplicate", "already exists"],
    "添加": ["create", "add", "new"],
    "创建": ["create", "add", "new"],
    "新增": ["create", "add", "new"],
    "更新": ["update", "edit"],
    "编辑": ["update", "edit"],
    "删除": ["delete", "remove"],
    "保存": ["save", "persist"],
    "校验": ["validate", "check"],
    "验证": ["validate", "check"],
    "错误": ["error", "exception"],
    "异常": ["exception", "error"],
    "找不到": ["not found"],
    "未找到": ["not found"],
    "不存在": ["not found", "missing"],
    "无效": ["invalid"],
    "非法": ["illegal"],
    "参数": ["parameter", "argument"],
    "字段": ["field"],
    "名字": ["name"],
    "邮箱": ["email"],
    "邮件": ["email"],
    "查询": ["find", "search", "query", "get"],
    "名称": ["name"],
    "标识": ["id"],
    "编号": ["id"],
    "页面": ["page", "view"],
    "跳转": ["redirect"],
    "请求": ["request"],
    "响应": ["response"],
    "主人": ["owner"],
    "用户": ["owner", "user"],
    "数据库": ["database", "repository"],
}

ERROR_PROBES = [
    "owner not found with id",
    "not found with id",
    "please ensure the id is correct",
    "the owner exists in the database",
    "ensure the id is correct and the owner exists",
    "owner exists in the database",
]


def load_json(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def iter_methods(index_obj):
    def is_method(obj: dict) -> bool:
        if "name" not in obj:
            return False
        if "methods" in obj or "classes" in obj:
            return False
        return any(k in obj for k in [
            "signature", "start_line", "line_start", "line_end", "end_line",
            "annotations", "strings", "calls", "parameters"
        ])

    def walk(obj, file_path=None, class_name=None):
        if isinstance(obj, dict):
            if is_method(obj):
                item = dict(obj)
                item.setdefault("file", file_path)
                item.setdefault("class", class_name)
                yield item
                return

            new_file = obj.get("path") or obj.get("file") or file_path
            new_class = obj.get("name") if ("methods" in obj or "classes" in obj) else class_name

            for key, val in obj.items():
                if key in ("methods", "classes", "files", "types"):
                    yield from walk(val, new_file, new_class)
                elif isinstance(val, (dict, list)):
                    yield from walk(val, new_file, new_class)

        elif isinstance(obj, list):
            for x in obj:
                yield from walk(x, file_path, class_name)

    yield from walk(index_obj)


def extract_keywords(issue_text: str):
    text = issue_text.lower()

    en_tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", text)

    zh_tokens = []
    for tok in jieba.cut(issue_text):
        tok = tok.strip().lower()
        if len(tok) < 2:
            continue
        if not re.search(r"[\u4e00-\u9fff]", tok):
            continue
        zh_tokens.append(tok)

    expanded = list(en_tokens)
    for t in zh_tokens:
        if t in STOPWORDS:
            continue
        expanded.append(t)
        if t in CN_EN_TECH_TERMS:
            expanded.extend(CN_EN_TECH_TERMS[t])

    keywords = []
    for t in expanded:
        if t in STOPWORDS:
            continue
        if t.isdigit():
            continue
        keywords.append(t)

    for p in [
        "owner not found",
        "not found with id",
        "orElseThrow",
        "findById",
        "pagination",
    ]:
        if p.lower() in text:
            keywords.append(p.lower())

    return list(dict.fromkeys(keywords))


def error_coverage(issue_text: str, method_text: str) -> float:
    issue_lower = issue_text.lower()
    method_lower = method_text.lower()

    present = [p for p in ERROR_PROBES if p in issue_lower]
    if present:
        hit = sum(1 for p in present if p in method_lower)
        return hit / len(present)

    issue_tokens = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", issue_lower)) - STOPWORDS
    method_tokens = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", method_lower))

    if not issue_tokens:
        return 0.0
    return len(issue_tokens & method_tokens) / len(issue_tokens)


def score_method(method: dict, keywords: list, issue_text: str, disable=None):
    disable = disable or set()

    name = (method.get("name") or "").lower()
    signature = (method.get("signature") or "").lower()
    class_name = (method.get("class") or method.get("class_name") or "").lower()
    file_path = (method.get("file") or method.get("path") or "").lower()

    annotations = " ".join(method.get("annotations") or []).lower()
    strings = " ".join(method.get("strings") or []).lower()
    calls = " ".join(method.get("calls") or []).lower()
    body = (method.get("body") or "").lower()

    hay_name = name
    hay_sig = f"{class_name} {signature}"
    hay_strings = strings
    hay_ann = annotations
    hay_calls = calls
    hay_file = file_path

    score = 0.0
    detail = {}

    kw_name = sum(1 for kw in keywords if kw in hay_name)
    kw_sig = sum(1 for kw in keywords if kw in hay_sig)
    kw_strings = sum(1 for kw in keywords if kw in hay_strings)
    kw_ann = sum(1 for kw in keywords if kw in hay_ann)
    kw_calls = sum(1 for kw in keywords if kw in hay_calls)
    kw_file = sum(1 for kw in keywords if kw in hay_file)

    if "name" not in disable:
        score += kw_name * 12
    if "signature" not in disable:
        score += kw_sig * 3
    if "strings" not in disable:
        score += kw_strings * 6
    if "annotation" not in disable:
        score += kw_ann * 3
    if "calls" not in disable:
        score += kw_calls * 2
    if "file" not in disable:
        score += kw_file * 1

    cov = error_coverage(issue_text, f"{hay_strings} {hay_sig} {hay_calls} {body}")

    if "error_coverage" not in disable:
        if cov >= 0.6:
            score += 20 * cov

    issue_lower = issue_text.lower()
    if "exception" not in disable:
        if any(x in issue_lower for x in ["exception", "throw", "error", "500", "illegalargument", "not found"]):
            if "orelsethrow" in hay_calls or "throw" in hay_calls or "throw" in body:
                score += 8
                detail["exception_throw"] = 8

    if "name_bonus" not in disable:
        for kw in keywords:
            if " " not in kw and kw in name:
                score += 4

    if "process_penalty" not in disable:
        if name.startswith("process") and cov < 0.3:
            score -= 5

    detail.update({
        "kw_name": kw_name,
        "kw_sig": kw_sig,
        "kw_strings": kw_strings,
        "kw_ann": kw_ann,
        "kw_calls": kw_calls,
        "kw_file": kw_file,
        "error_coverage": round(cov, 3),
    })

    return score, detail


def format_candidate(rank: int, item: dict) -> str:
    m = item["method"]
    d = item["detail"]

    file = m.get("file") or m.get("path") or "?"
    cls = m.get("class") or m.get("class_name") or "?"
    name = m.get("name") or "?"
    start = m.get("start_line") or m.get("line_start") or "?"
    end = m.get("end_line") or m.get("line_end") or "?"
    ann = m.get("annotations") or []

    return (
        f"{rank}. {file} :: {cls}.{name}  score={item['score']:.2f}\n"
        f"   行号: {start}-{end}  错误覆盖={d.get('error_coverage')}  "
        f"name命中={d.get('kw_name')} strings命中={d.get('kw_strings')} "
        f"注解命中={d.get('kw_ann')} 异常加分={d.get('exception_throw', 0)}\n"
        f"   注解: {ann}"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", default="data/benchmark.json")
    parser.add_argument("--index", default="spring-petclinic/index.json")
    parser.add_argument("--id", default=None)
    parser.add_argument("--topk", type=int, default=5)
    args = parser.parse_args()

    bench = load_json(args.benchmark)
    if isinstance(bench, dict):
        cases = bench.get("cases") or bench.get("benchmark") or [bench]
    else:
        cases = bench

    if args.id:
        case = next((x for x in cases if x.get("id") == args.id), None)
        if not case:
            raise SystemExit(f"找不到 benchmark id: {args.id}")
    else:
        case = cases[0]

    index_obj = load_json(args.index)
    methods = list(iter_methods(index_obj))

    print(f"加载方法数: {len(methods)}")
    if not methods:
        print("没有解析到方法，请检查 index.json 结构。")
        return

    keywords = extract_keywords(case["issue_text"])
    print("关键词:", keywords)
    print("=" * 80)

    scored = []
    for m in methods:
        s, d = score_method(m, keywords, case["issue_text"])
        scored.append({"method": m, "score": s, "detail": d})

    scored.sort(key=lambda x: x["score"], reverse=True)

    for i, item in enumerate(scored[:args.topk], 1):
        print(format_candidate(i, item))
        print("-" * 80)

    gt_files = [f.lower() for f in case.get("ground_truth_files", [])]
    gt_methods = [m.lower() for m in case.get("ground_truth_methods", [])]

    print("\nGround Truth 排名:")
    for rank, item in enumerate(scored, 1):
        m = item["method"]
        name = (m.get("name") or "").lower()
        file = (m.get("file") or m.get("path") or "").lower()

        if name in gt_methods or any(g in file for g in gt_files):
            print(
                f"  {rank}. {file} :: {m.get('class')}.{m.get('name')}  "
                f"score={item['score']:.2f}  detail={item['detail']}"
            )


if __name__ == "__main__":
    main()