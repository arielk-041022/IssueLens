# src/retriever.py
import json
import re
import argparse
from pathlib import Path
from issue_input import add_arguments, direct_case, record_input

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
    if "layer" not in disable:
        ann_text = hay_ann
        if "@controller" in ann_text or "@restcontroller" in ann_text:
            score += 6
        elif "@service" in ann_text:
            score += 4
        elif "@repository" in ann_text:
            score += 2
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


# ============================================================
# 展示层辅助函数（不改打分，只改输出）
# ============================================================

def detect_role(method: dict) -> str:
    """从注解、类名和方法名推断方法角色。只用于展示。"""
    ann = " ".join(method.get("annotations") or []).lower()
    name = (method.get("name") or "").lower()
    cls = (method.get("class") or method.get("class_name") or "").lower()

    if any(x in ann for x in ["@getmapping", "@postmapping", "@putmapping",
                              "@deletemapping", "@patchmapping", "@requestmapping"]):
        return "HTTP_ENTRY"
    if "@service" in ann:
        return "BUSINESS_LOGIC"
    if "@repository" in ann:
        return "DATA_ACCESS"

    is_entity_class = (
        "controller" not in cls
        and "service" not in cls
        and "repository" not in cls
        and "formatter" not in cls
        and "validator" not in cls
    )
    if is_entity_class and not ann:
        return "ENTITY"

    if name.startswith(("is", "has", "validate", "check", "build", "handle")):
        return "HELPER"

    return "UNKNOWN"


def confidence_label(score: float) -> str:
    if score >= 70:
        return "STRONG"
    if score >= 40:
        return "PLAUSIBLE"
    return "WEAK"


def confidence_mark(label: str) -> str:
    return {"STRONG": "[*]", "PLAUSIBLE": "[~]", "WEAK": "[.]"}.get(label, "[.]")


def negative_evidence(method: dict, detail: dict) -> list:
    """列出为什么这个候选可能不应该被选。纯展示。"""
    neg = []
    role = detect_role(method)
    ann = " ".join(method.get("annotations") or [])

    if role == "HELPER":
        neg.append("辅助方法（名字像 is/validate/check），不是请求入口")
    if role == "UNKNOWN" and not ann:
        neg.append("没有 Spring 注解，可能是内部方法")
    if role == "ENTITY":
        neg.append("实体类方法，不是请求处理入口")
    if detail.get("kw_strings", 0) == 0:
        neg.append("没有命中任何字符串常量")
    if detail.get("error_coverage", 0) == 0:
        neg.append("错误信息覆盖率为 0")

    return neg


def format_candidate(rank: int, item: dict) -> str:
    m = item["method"]
    d = item["detail"]
    score = item["score"]

    file = m.get("file") or m.get("path") or "?"
    cls = m.get("class") or m.get("class_name") or "?"
    name = m.get("name") or "?"
    ann = m.get("annotations") or []
    role = detect_role(m)
    label = confidence_label(score)
    mark = confidence_mark(label)

    lines = []
    signature = m.get("signature") or f"{name}()"
    lines.append(f"  {mark} [{rank}] {cls}.{signature}  score={score:.2f}  ({label})")
    lines.append(f"       role: {role}")
    lines.append(f"       file: {file}")

    if ann:
        lines.append(f"       annotations: {ann}")

    pos = []
    if d.get("kw_name", 0) > 0:
        pos.append(f"方法名命中 {d['kw_name']} 个关键词")
    if d.get("kw_strings", 0) > 0:
        pos.append(f"字符串常量命中 {d['kw_strings']} 处")
    if d.get("kw_ann", 0) > 0:
        pos.append(f"注解命中 {d['kw_ann']} 处")
    if d.get("kw_calls", 0) > 0:
        pos.append(f"调用关系命中 {d['kw_calls']} 处")
    if d.get("error_coverage", 0) > 0:
        pos.append(f"错误信息覆盖率 {d['error_coverage']}")
    if d.get("exception_throw", 0) > 0:
        pos.append(f"含异常抛出（+{d['exception_throw']}）")

    if pos:
        lines.append("       positive evidence:")
        for p in pos:
            lines.append(f"         + {p}")

    neg = negative_evidence(m, d)
    if neg:
        lines.append("       negative evidence:")
        for n in neg:
            lines.append(f"         - {n}")

    return "\n".join(lines)


def method_key(method: dict) -> str:
    """跨模块复用的方法唯一标识：Class#name。"""
    cls = method.get("class") or method.get("class_name") or "?"
    name = method.get("name") or "?"
    return f"{cls}#{name}"


def render_case_block(case, scored, topk, *, mode="retriever", extra=None) -> str:
    """统一的展示块渲染，retriever 与 llm_reranker 共用。

    mode="retriever"：只显示 Top-k 与证据
    mode="rerank"   ：额外显示 rank 迁移（rule #x → LLM #y）与 LLM Rationale

    extra 支持键：
      methods_count   : int
      keywords        : list
      provider        : "deepseek" | "qwen"
      dry_run         : bool
      fallback        : bool   # True 时隐藏 rank 迁移行，标题打 [fallback: rule-only]
      reason          : str    # LLM Rationale 段落
      rank_from_rule  : dict   # {method_key: rule_rank}
    """
    extra = extra or {}
    methods_count = extra.get("methods_count")
    keywords = extra.get("keywords") or []
    fallback = bool(extra.get("fallback"))

    out = []
    out.append("=" * 70)
    out.append(" IssueLens — Evidence-Based Issue-to-Code Localization")
    if mode == "rerank":
        provider = extra.get("provider", "deepseek")
        model_name = {"deepseek": "DeepSeek-chat", "qwen": "qwen-plus"}.get(provider, provider)
        tag = f" Rerank: {model_name}"
        if extra.get("dry_run"):
            tag += "   [dry-run]"
        if fallback:
            tag += "   [fallback: rule-only]"
        out.append(tag)
    out.append("=" * 70)
    out.append(f" Case ID    : {case.get('id')}")
    out.append(f" Repo       : {case.get('repo_path')}")
    if methods_count is not None:
        out.append(f" Methods    : {methods_count} indexed")
    if keywords:
        out.append(f" Keywords   : {keywords}")
    out.append("-" * 70)
    out.append(" Issue:")
    issue_preview = (case.get("issue_text") or "").strip()
    if len(issue_preview) > 200:
        issue_preview = issue_preview[:200] + "... [显示预览；检索使用完整原文]"
    out.append(f"   {issue_preview}")
    out.append("=" * 70)

    header = f" Localization Top-{topk}"
    if mode == "rerank":
        header += "  (after rerank)"
    out.append(header)
    out.append("=" * 70)

    rank_from_rule = extra.get("rank_from_rule") or {}

    for i, item in enumerate(scored[:topk], 1):
        block = format_candidate(i, item)
        if mode == "rerank" and not fallback and rank_from_rule:
            key = method_key(item["method"])
            rule_rank = rank_from_rule.get(key)
            if rule_rank is not None:
                if rule_rank == i:
                    arrow = f"       rank: rule #{rule_rank} (unchanged)"
                else:
                    arrow = f"       rank: rule #{rule_rank} → LLM #{i}"
                lines_split = block.split("\n")
                insert_at = None
                for idx, ln in enumerate(lines_split):
                    if ln.strip().startswith("role:"):
                        insert_at = idx + 1
                        break
                if insert_at is None:
                    insert_at = 2
                lines_split.insert(insert_at, arrow)
                block = "\n".join(lines_split)
        out.append(block)
        out.append("-" * 70)

    if mode == "rerank" and not fallback:
        reason = extra.get("reason")
        if reason:
            out.append(" LLM Rationale:")
            for ln in str(reason).strip().splitlines():
                out.append(f"   {ln}")
            out.append("-" * 70)

    if case.get("test_command"):
        out.append(" Suggested verification:")
        out.append(f"   {case['test_command']}")
        out.append("=" * 70)

    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", default="data/benchmark.json")
    parser.add_argument("--index", default=None,
                        help="已有索引；直接输入默认使用 --repo/index.json，旧模式默认不变")
    parser.add_argument("--id", default=None)
    parser.add_argument("--topk", type=int, default=5)
    add_arguments(parser)
    args = parser.parse_args()

    case = direct_case(args, parser)
    if case is None:
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
    case = record_input(case, args, parser)

    index_path = args.index or (str(Path(args.repo) / 'index.json') if args.repo else 'spring-petclinic/index.json')
    index_obj = load_json(index_path)
    methods = list(iter_methods(index_obj))

    if not methods:
        print("没有解析到方法，请检查 index.json 结构。")
        return

    keywords = extract_keywords(case["issue_text"])

    scored = []
    for m in methods:
        s, d = score_method(m, keywords, case["issue_text"])
        scored.append({"method": m, "score": s, "detail": d})
    scored.sort(key=lambda x: x["score"], reverse=True)

    print(render_case_block(
        case, scored, args.topk,
        mode="retriever",
        extra={"methods_count": len(methods), "keywords": keywords},
    ))

    gt_files = [f.lower() for f in case.get("ground_truth_files", [])]
    gt_methods = [m.lower() for m in case.get("ground_truth_methods", [])]

    print("\n Ground Truth 排名:")
    for rank, item in enumerate(scored, 1):
        m = item["method"]
        name = (m.get("name") or "").lower()
        file = (m.get("file") or m.get("path") or "").lower()

        if name in gt_methods or any(g in file for g in gt_files):
            print(
                f"   {rank}. {file} :: {m.get('class')}.{m.get('name')}  "
                f"score={item['score']:.2f}"
            )


if __name__ == "__main__":
    main()
