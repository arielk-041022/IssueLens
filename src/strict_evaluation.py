"""TASK 003: independent strict GT audit, unchanged rule scoring, offline ranking replay."""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile

HERE = Path(__file__).resolve().parent
IDENTITY_FIELDS = ("repo_id", "source_snapshot_id", "file_path", "method_id")
TASK_PHASE = {"PRE_FIX_EXISTING_METHOD_LOCALIZATION": "PRE_FIX",
              "REFERENCE_LOCATION_RETRIEVAL": "FIXED_REFERENCE"}
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")


class ProtocolError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise ProtocolError(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(obj):
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "DUPLICATE_JSON_KEY: " + key)
            result[key] = value
        return result
    def constant(value):
        raise ProtocolError("NONSTANDARD_JSON_NUMBER: " + value)
    raw = Path(path).read_bytes()
    obj = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    # Reject unpaired surrogate strings even when escaped in otherwise valid JSON.
    def strings(item):
        if isinstance(item, str):
            item.encode("utf-8", errors="strict")
        elif isinstance(item, dict):
            for key, value in item.items():
                strings(key); strings(value)
        elif isinstance(item, list):
            for value in item:
                strings(value)
    strings(obj)
    return obj, digest(raw)


def load_local(name):
    spec = importlib.util.spec_from_file_location("issuelens_task003_" + name, HERE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def text(value):
    return isinstance(value, str) and bool(value)


def is_hash(value):
    return isinstance(value, str) and HASH_RE.fullmatch(value) is not None


def canonical_file(value):
    if not text(value) or "\\" in value or ":" in value or value.startswith("/"):
        return False
    parts = value.split("/")
    return (parts[:3] == ["src", "main", "java"] and len(parts) >= 4 and value.endswith(".java")
            and all(p and p not in {".", ".."} and not p.endswith((".", " ")) for p in parts)
            and all(ord(c) >= 32 for c in value))


def identity(repo, snapshot, file, method_id):
    return dict(zip(IDENTITY_FIELDS, (repo, snapshot, file, method_id)))


def identity_key(record):
    require(isinstance(record, dict), "INVALID_IDENTITY_OBJECT")
    require(all(text(record.get(k)) for k in IDENTITY_FIELDS), "MISSING_STRICT_IDENTITY")
    require(canonical_file(record["file_path"]), "NONCANONICAL_FILE_PATH")
    require(record["method_id"].startswith("java-source-v1:"), "INVALID_METHOD_ID_SCHEME")
    return tuple(record[k] for k in IDENTITY_FIELDS)


def manifest_for(index):
    return sorted([dict(file_path=f["file"], source_sha256=f["source_sha256"]) for f in index],
                  key=lambda f: f["file_path"])


def manifest_hash(manifest):
    # A source snapshot is a sorted list of exact relative file names and byte hashes.
    raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return digest(raw)


def build_method_catalog(index, repo_id, snapshot_id):
    """No name-based upgrade, no generated ID for legacy methods."""
    catalog = {}
    for f in index:
        require(canonical_file(f.get("file")), "INVALID_INDEX_FILE_PATH")
        require(f.get("parse_status") == "PARSED", "INDEX_PARSE_UNAVAILABLE")
        for c in f.get("classes", []):
            for m in c.get("methods", []):
                require(text(m.get("method_id")), "LEGACY_OR_MISSING_METHOD_ID")
                require(m.get("identity_status") == "AVAILABLE", "METHOD_ID_UNAVAILABLE_OR_CONFLICT")
                require(m.get("method_id") == "java-source-v1:" + m.get("qualified_class_name", "")
                        + "#" + m.get("signature", ""), "METHOD_ID_DECLARATION_MISMATCH")
                require(m.get("qualified_class_name", "").split(".")[-1] == c.get("name")
                        and m.get("signature", "").startswith(m.get("name", "") + "(")
                        and m.get("signature", "").endswith(")"), "METHOD_CONTAINER_MISMATCH")
                key = identity_key(identity(repo_id, snapshot_id, f["file"], m["method_id"]))
                require(key not in catalog, "METHOD_ID_CONFLICT")
                catalog[key] = m
    # ID must be unique throughout the repository, even if copied to a different file.
    require(len({k[3] for k in catalog}) == len(catalog), "METHOD_ID_CONFLICT")
    return catalog


def validate_order(records, catalog, repo_id, snapshot_id):
    require(isinstance(records, list), "INVALID_RANKING_ARRAY")
    keys = [identity_key(record) for record in records]
    require(len(keys) == len(set(keys)), "DUPLICATE_CANDIDATE")
    for record, key in zip(records, keys):
        require(key[:2] == (repo_id, snapshot_id), "CANDIDATE_SNAPSHOT_OR_REPO_MISMATCH")
        require(key in catalog, "UNKNOWN_CANDIDATE_IDENTITY")
        if "score" in record:
            require(type(record["score"]) in (int, float) and math.isfinite(record["score"]), "INVALID_SCORE")
    return keys


def evaluate_order(order, targets, file_targets, required_groups=()):
    """Pure exact matching. Caller has already frozen eligibility and validated identities."""
    target_keys = set(targets)
    rank = next((i for i, key in enumerate(order, 1) if key in target_keys), None)
    group_hits = [any(key in set(group) for key in order[:5]) for group in required_groups]
    return {
        "strict_method_top1": rank == 1,
        "strict_method_top5": rank is not None and rank <= 5,
        "strict_file_top1": bool(order) and order[0][2] in set(file_targets),
        "first_strict_rank": rank, "reciprocal_rank": 0.0 if rank is None else 1.0 / rank,
        "required_group_coverage_at5": None if not group_hits else sum(group_hits) / len(group_hits),
    }


def _target(target):
    require(isinstance(target, dict) and canonical_file(target.get("file_path"))
            and text(target.get("method_id")) and target["method_id"].startswith("java-source-v1:"),
            "INVALID_GT_TARGET")


def validate_gt_sidecar(benchmark, benchmark_hash, sidecar):
    require(isinstance(benchmark, list), "BENCHMARK_MUST_BE_ARRAY")
    require(all(isinstance(c, dict) and text(c.get("id")) and text(c.get("issue_text")) for c in benchmark),
            "INVALID_BENCHMARK_CASE")
    ids = [c["id"] for c in benchmark]
    require(len(ids) == len(set(ids)), "DUPLICATE_BENCHMARK_CASE")
    require(isinstance(sidecar, dict) and sidecar.get("schema_version") == "1.0", "INVALID_SIDECAR_SCHEMA")
    require(sidecar.get("benchmark_sha256") == benchmark_hash, "BENCHMARK_HASH_MISMATCH")
    protocol = sidecar.get("protocol")
    require(isinstance(protocol, dict) and protocol.get("version") == "strict-localization-v1"
            and protocol.get("evaluation_task") in TASK_PHASE
            and protocol.get("primary_hit_policy") == "ANY_CONFIRMED_TARGET"
            and protocol.get("identity_scheme") == "java-source-v1", "INVALID_GT_PROTOCOL")
    snapshots = sidecar.get("snapshots")
    require(isinstance(snapshots, list), "INVALID_SNAPSHOTS")
    snapshot_map = {}
    for s in snapshots:
        require(isinstance(s, dict) and text(s.get("repo_id")) and text(s.get("source_snapshot_id")), "INVALID_SNAPSHOT")
        key = (s["repo_id"], s["source_snapshot_id"])
        require(key not in snapshot_map, "DUPLICATE_SNAPSHOT")
        require(s.get("phase") in {"PRE_FIX", "FIXED_REFERENCE"}, "INVALID_SNAPSHOT_PHASE")
        for field in ("index_sha256", "metadata_sha256", "source_manifest_sha256"):
            require(is_hash(s.get(field)), "MISSING_SNAPSHOT_BINDING: " + field)
        manifest = s.get("source_manifest")
        require(isinstance(manifest, list) and all(isinstance(f, dict) and canonical_file(f.get("file_path"))
                and is_hash(f.get("source_sha256")) for f in manifest), "INVALID_SOURCE_MANIFEST")
        require(len({f["file_path"] for f in manifest}) == len(manifest), "DUPLICATE_MANIFEST_FILE")
        require(manifest == sorted(manifest, key=lambda f: f["file_path"]), "UNSORTED_SOURCE_MANIFEST")
        mh = manifest_hash(manifest)
        require(mh == s["source_manifest_sha256"] and s["source_snapshot_id"] == "sha256:" + mh,
                "SOURCE_SNAPSHOT_ID_MISMATCH")
        snapshot_map[key] = s
    cases = sidecar.get("cases")
    require(isinstance(cases, list) and all(isinstance(c, dict) and text(c.get("case_id")) for c in cases), "INVALID_SIDECAR_CASES")
    case_ids = [c["case_id"] for c in cases]
    require(len(case_ids) == len(set(case_ids)) and set(case_ids) == set(ids), "SIDECAR_CASE_COVERAGE_MISMATCH")
    for c in cases:
        require((c.get("repo_id"), c.get("source_snapshot_id")) in snapshot_map, "UNKNOWN_GT_SNAPSHOT")
        require(c.get("annotation_status") in ("CONFIRMED", "PENDING_REVIEW", "REJECTED"), "INVALID_ANNOTATION_STATUS")
        require(c.get("file_gt_status") in ("CONFIRMED", "PENDING_REVIEW", "REJECTED"), "INVALID_FILE_GT_STATUS")
        require(c.get("target_set_status") in ("FROZEN", "NOT_FROZEN"), "INVALID_TARGET_SET_STATUS")
        for field in ("accepted_targets", "proposed_targets"):
            require(isinstance(c.get(field), list), "INVALID_TARGET_ARRAY")
            for target in c[field]:
                _target(target)
            require(len({(t["file_path"], t["method_id"]) for t in c[field]}) == len(c[field]), "DUPLICATE_GT_TARGET")
        require(isinstance(c.get("accepted_files"), list) and all(canonical_file(p) for p in c["accepted_files"])
                and len(c["accepted_files"]) == len(set(c["accepted_files"])), "INVALID_ACCEPTED_FILES")
        if c["annotation_status"] == "CONFIRMED" and c["file_gt_status"] == "CONFIRMED":
            require(all(t["file_path"] in c["accepted_files"] for t in c["accepted_targets"]), "METHOD_FILE_GT_INCONSISTENT")
        require(isinstance(c.get("reason_codes"), list) and all(text(r) for r in c["reason_codes"]), "INVALID_GT_REASONS")
        require(isinstance(c.get("basis"), list) and isinstance(c.get("review"), dict), "INVALID_REVIEW_BASIS")
        require(isinstance(c.get("required_groups"), list), "INVALID_REQUIRED_GROUPS")
        group_ids = []
        accepted = {(t["file_path"], t["method_id"]) for t in c["accepted_targets"]}
        for group in c["required_groups"]:
            require(isinstance(group, dict) and text(group.get("group_id")) and isinstance(group.get("targets"), list)
                    and bool(group["targets"]), "INVALID_REQUIRED_GROUP")
            group_ids.append(group["group_id"])
            for target in group["targets"]:
                _target(target)
                require((target["file_path"], target["method_id"]) in accepted, "GROUP_TARGET_NOT_ACCEPTED")
        require(len(group_ids) == len(set(group_ids)), "DUPLICATE_REQUIRED_GROUP")
    return snapshot_map, {c["case_id"]: c for c in cases}


def review_ready(case):
    review = case["review"]
    return (text(review.get("reviewer")) and text(review.get("reviewed_at"))
            and any(isinstance(b, dict) and b.get("kind") in ("PATCH", "TEST_BEHAVIOR", "HUMAN_REVIEW")
                    and text(b.get("reference")) and is_hash(b.get("sha256")) for b in case["basis"]))


def resolve_ground_truth(case, snapshot, catalog, valid_snapshot, task):
    repo, sid = case["repo_id"], case["source_snapshot_id"]
    common = []
    if not valid_snapshot:
        common.append("SNAPSHOT_UNAVAILABLE")
    if snapshot["phase"] != TASK_PHASE[task]:
        common.append("SNAPSHOT_PHASE_MISMATCH")
    method_reasons, file_reasons = list(common), list(common)
    if case["annotation_status"] != "CONFIRMED":
        method_reasons.append("GT_" + case["annotation_status"])
    if case["target_set_status"] != "FROZEN":
        method_reasons.append("TARGET_SET_NOT_FROZEN")
    if not case["accepted_targets"]:
        method_reasons.append("NO_CONFIRMED_TARGETS")
    if not review_ready(case):
        method_reasons.append("INDEPENDENT_REVIEW_BASIS_MISSING")
        file_reasons.append("INDEPENDENT_REVIEW_BASIS_MISSING")
    targets = [identity_key(identity(repo, sid, t["file_path"], t["method_id"])) for t in case["accepted_targets"]]
    if valid_snapshot and any(t not in catalog for t in targets):
        method_reasons.append("GT_NOT_IN_SNAPSHOT_CATALOG")
    if case["file_gt_status"] != "CONFIRMED":
        file_reasons.append("FILE_GT_" + case["file_gt_status"])
    if not case["accepted_files"]:
        file_reasons.append("NO_CONFIRMED_FILES")
    catalog_files = {f["file_path"] for f in snapshot["source_manifest"]}
    if valid_snapshot and any(p not in catalog_files for p in case["accepted_files"]):
        file_reasons.append("FILE_GT_NOT_IN_SNAPSHOT_CATALOG")
    return dict(method_eligible=not method_reasons, file_eligible=not file_reasons,
                method_reasons=method_reasons, file_reasons=file_reasons, targets=targets)


def _snapshot_context(snapshot, repo_paths, index_paths, validator):
    repo, sid = snapshot["repo_id"], snapshot["source_snapshot_id"]
    require(repo in repo_paths and repo in index_paths, "MISSING_REPOSITORY_OR_INDEX_BINDING: " + repo)
    index_path = Path(index_paths[repo])
    index, ih = read_json(index_path)
    _, mh = read_json(str(index_path) + ".meta.json")
    require(ih == snapshot["index_sha256"] and mh == snapshot["metadata_sha256"], "SNAPSHOT_INDEX_BINDING_MISMATCH")
    require(isinstance(index, list) and manifest_for(index) == snapshot["source_manifest"], "SNAPSHOT_SOURCE_MANIFEST_MISMATCH")
    root = Path(repo_paths[repo]).resolve(strict=True)
    source_root = root / "src/main/java"
    # Binding every indexed file is insufficient if a new source file has appeared.
    actual_paths = {p.relative_to(root).as_posix() for p in source_root.rglob("*.java") if p.is_file()}
    require(actual_paths == {f["file_path"] for f in snapshot["source_manifest"]}, "SNAPSHOT_SOURCE_SET_MISMATCH")
    proof = validator.validate(repo_paths[repo], index_path)
    require(proof["report_status"] == "COMPLETE" and proof["overall_verdict"] == "PASS", "SOURCE_VALIDATION_FAILED: "
            + json.dumps([i["reason_code"] for i in proof["issues"]], sort_keys=True))
    require(proof["inputs"]["index_sha256"] == ih and proof["inputs"]["metadata_sha256"] == mh, "INDEX_CHANGED_DURING_AUDIT")
    catalog = build_method_catalog(index, repo, sid)
    return dict(snapshot=snapshot, index=index, catalog=catalog,
                proof=proof, status="VALID", index_sha256=ih, metadata_sha256=mh)


def _score_rule(benchmark, case_map, contexts, candidate_k, runtime_root):
    scorer = load_local("retriever")  # direct calls to existing functions, no copied scoring logic
    root = Path(runtime_root) if runtime_root else Path(tempfile.gettempdir())
    root.mkdir(parents=True, exist_ok=True)
    cache_dir = tempfile.mkdtemp(prefix="issuelens-task003-jieba-", dir=root)
    old_dir = scorer.jieba.dt.tmp_dir
    scorer.jieba.dt.tmp_dir = cache_dir
    cases = []
    try:
        for case in benchmark:
            gt = case_map[case["id"]]
            ctx = contexts[(gt["repo_id"], gt["source_snapshot_id"])]
            require(ctx["status"] == "VALID", "CANNOT_RANK_INVALID_SNAPSHOT")
            methods = list(scorer.iter_methods(ctx["index"]))
            keywords = scorer.extract_keywords(case["issue_text"])
            scored = []
            for method in methods:
                score, detail = scorer.score_method(method, keywords, case["issue_text"])
                row = identity(gt["repo_id"], gt["source_snapshot_id"], method["file"], method["method_id"])
                row.update(score=score, detail=detail)
                scored.append(row)
            scored.sort(key=lambda row: row["score"], reverse=True)  # stable original traversal order
            cases.append(dict(case_id=case["id"], repo_id=gt["repo_id"], source_snapshot_id=gt["source_snapshot_id"],
                              retrieval_ranking=scored, candidate_k=candidate_k,
                              rerank=dict(status="NOT_REQUESTED", ranking=[])))
    finally:
        scorer.jieba.dt.tmp_dir = old_dir
    return cases, getattr(scorer.jieba, "__version__", "UNKNOWN")


def evaluate_ranked_case(row, gt, eligibility, catalog, stage):
    repo, sid = gt["repo_id"], gt["source_snapshot_id"]
    require(row.get("repo_id") == repo and row.get("source_snapshot_id") == sid, "RANKING_CASE_SNAPSHOT_MISMATCH")
    order = validate_order(row.get("retrieval_ranking"), catalog, repo, sid)
    k = row.get("candidate_k")
    require(type(k) is int and k > 0, "INVALID_CANDIDATE_K")
    prefix = order[:k]
    rerank = row.get("rerank")
    require(isinstance(rerank, dict) and rerank.get("status") in
            ("VALID", "FALLBACK", "UNAVAILABLE", "DRY_RUN", "NOT_REQUESTED"), "INVALID_RERANK_STATUS")
    status = rerank["status"]
    if status == "VALID":
        new_prefix = validate_order(rerank.get("ranking"), catalog, repo, sid)
        require(len(new_prefix) == len(prefix) and set(new_prefix) == set(prefix), "INVALID_RERANK_PERMUTATION")
    else:
        require(rerank.get("ranking", []) == [] or validate_order(rerank["ranking"], catalog, repo, sid) == prefix,
                "FALLBACK_REORDERED_CANDIDATES")
        new_prefix = None
    targets = eligibility["targets"]
    groups = [[identity_key(identity(repo, sid, t["file_path"], t["method_id"])) for t in group["targets"]]
              for group in gt["required_groups"]]
    chosen = order if stage == "rule" else None if new_prefix is None else new_prefix + order[k:]
    scores = None if chosen is None else evaluate_order(chosen, targets, gt["accepted_files"], groups)
    result = dict(ranking_status="VALID" if chosen is not None else "RERANK_UNAVAILABLE",
                  rerank_status=status, retrieval_size=len(order), candidate_size=len(prefix),
                  method_metrics=None, file_metrics=None, failure_class=None)
    if eligibility["method_eligible"]:
        if scores is not None:
            result["method_metrics"] = {key: scores[key] for key in
                                        ("strict_method_top1", "strict_method_top5", "first_strict_rank",
                                         "reciprocal_rank", "required_group_coverage_at5")}
        if not any(key in set(targets) for key in prefix):
            result["failure_class"] = "RETRIEVAL_CANDIDATE_MISS"
        elif chosen is None:
            result["failure_class"] = "RERANK_UNAVAILABLE"
        elif not scores["strict_method_top1"]:
            result["failure_class"] = "RERANKING_ERROR" if stage == "rerank" else "RULE_ORDERING_ERROR"
        else:
            result["failure_class"] = "HIT"
    if eligibility["file_eligible"] and scores is not None:
        result["file_metrics"] = {"strict_file_top1": scores["strict_file_top1"]}
    return result


def summarize_cases(rows, denominators, mode, mrr_name, invalid_run=False):
    cm, cf, total = denominators["method"], denominators["file"], denominators["total"]
    method_ready = sum(r.get("method_metrics") is not None for r in rows)
    file_ready = sum(r.get("file_metrics") is not None for r in rows)
    usable_m = mode != "audit" and not invalid_run and cm > 0 and method_ready == cm
    usable_f = mode != "audit" and not invalid_run and cf > 0 and file_ready == cf
    def metric(key, kind, denominator, usable):
        return dict(status="EVALUATED" if usable else "NOT_EVALUABLE", denominator=denominator,
                    numerator=sum(r[kind][key] for r in rows if r.get(kind) is not None) if usable else None,
                    value=sum(r[kind][key] for r in rows if r.get(kind) is not None) / denominator if usable else None)
    return {
        "strict_method_top1": metric("strict_method_top1", "method_metrics", cm, usable_m),
        "strict_method_top5": metric("strict_method_top5", "method_metrics", cm, usable_m),
        "strict_file_top1": metric("strict_file_top1", "file_metrics", cf, usable_f),
        mrr_name: metric("reciprocal_rank", "method_metrics", cm, usable_m),
        "method_coverage": dict(numerator=cm, denominator=total, value=cm / total if total else None),
        "file_coverage": dict(numerator=cf, denominator=total, value=cf / total if total else None),
        "pending_gt_cases": sum(r["annotation_status"] == "PENDING_REVIEW" for r in rows),
        "method_not_evaluable_cases": total - cm,
        "method_ranking_results_available": method_ready,
        "file_ranking_results_available": file_ready,
    }


def _artifact_header(report, sidecar, cases, candidate_k):
    return dict(schema_version="strict-ranking-v1", benchmark_sha256=report["inputs"]["benchmark_sha256"],
                gt_sidecar_sha256=report["inputs"]["sidecar_sha256"],
                scoring_code_sha256=report["inputs"]["scoring_code_sha256"],
                feature_profile="AS_IS_V2", ranking_scope="FULL", rank_limit=None,
                snapshots=[{k: s[k] for k in ("repo_id", "source_snapshot_id", "index_sha256", "metadata_sha256")}
                           for s in sidecar["snapshots"]], cases=cases)


def _validate_artifact(artifact, report, sidecar, benchmark):
    require(isinstance(artifact, dict) and artifact.get("schema_version") == "strict-ranking-v1", "LEGACY_OR_INVALID_RANKING_FORMAT")
    for a, b in (("benchmark_sha256", "benchmark_sha256"), ("gt_sidecar_sha256", "sidecar_sha256"),
                 ("scoring_code_sha256", "scoring_code_sha256")):
        require(artifact.get(a) == report["inputs"][b], "RANKING_INPUT_HASH_MISMATCH: " + a)
    expected = _artifact_header(report, sidecar, [], 5)["snapshots"]
    require(artifact.get("snapshots") == expected, "RANKING_SNAPSHOT_BINDING_MISMATCH")
    require(artifact.get("feature_profile") == "AS_IS_V2", "UNSUPPORTED_FEATURE_PROFILE")
    scope = artifact.get("ranking_scope")
    require(scope in ("FULL", "TOP_K"), "INVALID_RANKING_SCOPE")
    limit = artifact.get("rank_limit")
    require((scope == "FULL" and limit is None) or (scope == "TOP_K" and type(limit) is int and limit >= 5),
            "INVALID_RANK_LIMIT_FOR_TOP5")
    cases = artifact.get("cases")
    require(isinstance(cases, list) and all(isinstance(r, dict) and text(r.get("case_id")) for r in cases), "INVALID_RANKING_CASES")
    ids = [r["case_id"] for r in cases]
    require(len(ids) == len(set(ids)) and set(ids) == {c["id"] for c in benchmark}, "RANKING_CASE_COVERAGE_MISMATCH")
    if scope == "TOP_K":
        require(all(isinstance(c.get("retrieval_ranking"), list) and len(c["retrieval_ranking"]) <= limit for c in cases),
                "RANKING_EXCEEDS_DECLARED_LIMIT")
    return {r["case_id"]: r for r in cases}, "MRR" if scope == "FULL" else "MRR@" + str(limit)


def run_evaluation(benchmark_path, sidecar_path, repo_paths, index_paths, mode="audit", rankings=None,
                   stage="rule", candidate_k=5, runtime_root=None):
    report = dict(report_schema_version="1.0", protocol_version="strict-localization-v1", mode=mode, stage=stage,
                  report_status="COMPLETE", tool_status="OK", evaluation_status="NOT_EVALUABLE", exit_code=0,
                  inputs={}, snapshot_results=[], cases=[], issues=[], denominators=dict(total=0, method=0, file=0,
                  frozen_before_ranking=False), scope=dict(identity_fields=list(IDENTITY_FIELDS),
                  independent_gt_confirmation=False, independent_ast_parse=False, statistical_significance_claim=False))
    mrr_name = "MRR"
    try:
        require(mode in ("audit", "rule", "replay") and stage in ("rule", "rerank"), "INVALID_MODE_OR_STAGE")
        require(type(candidate_k) is int and candidate_k > 0, "INVALID_CANDIDATE_K")
        require(mode != "rule" or stage == "rule", "RULE_MODE_CANNOT_CLAIM_LLM_RERANK")
        bench, bh = read_json(benchmark_path)
        report["inputs"].update(benchmark_sha256=bh, scoring_code_sha256=digest((HERE / "retriever.py").read_bytes()),
                                validator_code_sha256=digest((HERE / "evidence_validator.py").read_bytes()),
                                evaluation_code_sha256=digest(Path(__file__).read_bytes()))
        if isinstance(bench, list):
            report["cases"] = [dict(case_id=c.get("id"), annotation_status="UNKNOWN", method_eligible=False,
                                     file_eligible=False, method_reasons=["INPUT_NOT_VALIDATED"],
                                     file_reasons=["INPUT_NOT_VALIDATED"]) for c in bench if isinstance(c, dict)]
            report["denominators"]["total"] = len(bench)
        sidecar, sh = read_json(sidecar_path)
        report["inputs"]["sidecar_sha256"] = sh
        snapshot_map, case_map = validate_gt_sidecar(bench, bh, sidecar)
        validator = load_local("evidence_validator")
        contexts = {}
        for key, snapshot in snapshot_map.items():
            try:
                ctx = _snapshot_context(snapshot, repo_paths, index_paths, validator)
                contexts[key] = ctx
                report["snapshot_results"].append(dict(repo_id=key[0], source_snapshot_id=key[1], status="VALID",
                    index_sha256=ctx["index_sha256"], metadata_sha256=ctx["metadata_sha256"],
                    source_validation_summary=ctx["proof"]["summary"]))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                contexts[key] = dict(status="INVALID", catalog={}, index=[])
                reason = type(exc).__name__ + ": " + str(exc)
                report["snapshot_results"].append(dict(repo_id=key[0], source_snapshot_id=key[1], status="INVALID", reason=reason))
                report["issues"].append(dict(stage="SNAPSHOT", reason_code="SNAPSHOT_UNAVAILABLE", detail=reason))
        report["cases"] = []
        for case in bench:
            gt = case_map[case["id"]]
            key = (gt["repo_id"], gt["source_snapshot_id"])
            ctx = contexts[key]
            eligibility = resolve_ground_truth(gt, snapshot_map[key], ctx["catalog"], ctx["status"] == "VALID",
                                               sidecar["protocol"]["evaluation_task"])
            report["cases"].append(dict(case_id=case["id"], repo_id=gt["repo_id"], source_snapshot_id=gt["source_snapshot_id"],
                annotation_status=gt["annotation_status"], annotation_reason_codes=gt["reason_codes"],
                **eligibility, ranking_status="NOT_REQUESTED", method_metrics=None, file_metrics=None))
        report["denominators"] = dict(total=len(bench), method=sum(r["method_eligible"] for r in report["cases"]),
                                      file=sum(r["file_eligible"] for r in report["cases"]), frozen_before_ranking=True)
        # No ranking file is opened and no scoring is invoked before this point.
        artifact = None
        if mode == "rule" and not report["issues"]:
            ranked, version = _score_rule(bench, case_map, contexts, candidate_k, runtime_root)
            report["inputs"]["jieba_version"] = version
            artifact = _artifact_header(report, sidecar, ranked, candidate_k)
        elif mode == "replay":
            require(rankings is not None, "REPLAY_REQUIRES_RANKINGS")
            saved, rh = read_json(rankings)
            report["inputs"]["rankings_sha256"] = rh
            require(isinstance(saved, dict), "INVALID_RANKING_DOCUMENT")
            artifact = saved.get("ranking_artifact", saved)
        if artifact is not None:
            ranked_map, mrr_name = _validate_artifact(artifact, report, sidecar, bench)
            report["ranking_artifact"] = artifact
            for row in report["cases"]:
                gt = case_map[row["case_id"]]
                ctx = contexts[(gt["repo_id"], gt["source_snapshot_id"])]
                try:
                    require(ctx["status"] == "VALID", "RANKING_SNAPSHOT_UNAVAILABLE")
                    row.update(evaluate_ranked_case(ranked_map[row["case_id"]], gt, row, ctx["catalog"], stage))
                    if row["ranking_status"] != "VALID" and (row["method_eligible"] or row["file_eligible"]):
                        report["issues"].append(dict(stage="RANKING", case_id=row["case_id"], reason_code="RERANK_UNAVAILABLE"))
                except (ValueError, KeyError, TypeError) as exc:
                    row.update(ranking_status="INVALID_RANKING", method_metrics=None, file_metrics=None,
                               ranking_reason=str(exc))
                    report["issues"].append(dict(stage="RANKING", case_id=row["case_id"], reason_code="INVALID_RANKING", detail=str(exc)))
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        report["issues"].append(dict(stage="INPUT", reason_code="INVALID_INPUT", detail=type(exc).__name__ + ": " + str(exc)))
    except Exception as exc:
        report["report_status"] = "ABORTED"
        report["issues"].append(dict(stage="EXECUTION", reason_code="UNEXPECTED_ERROR", detail=type(exc).__name__ + ": " + str(exc)))
    invalid = bool(report["issues"])
    report["metrics"] = summarize_cases(report["cases"], report["denominators"], mode, mrr_name, invalid)
    if invalid:
        report.update(tool_status="INVALID_RUN", evaluation_status="INVALID_RUN", exit_code=2)
        # Preserve diagnostics but never publish partial aggregate scores as authoritative.
    elif mode != "audit" and (report["denominators"]["method"] or report["denominators"]["file"]):
        report["evaluation_status"] = "EVALUATED"
    return report


def write_report(path, report, protected=(), repo_paths=()):
    output = Path(path).absolute()
    resolved = output.resolve(strict=False)
    require(all(resolved != Path(p).resolve(strict=False) for p in protected), "OUTPUT_IS_INPUT")
    for repo in repo_paths:
        root = Path(repo).resolve(strict=False)
        require(resolved != root and root not in resolved.parents, "OUTPUT_INSIDE_TARGET_REPOSITORY")
    output.parent.mkdir(parents=True, exist_ok=True)
    raw = json_bytes(report)
    created = False
    try:
        with output.open("xb") as handle:
            created = True
            handle.write(raw)
    except Exception:
        if created:
            output.unlink(missing_ok=True)
        raise


def bindings(values):
    result = {}
    for value in values:
        require("=" in value, "BINDING_MUST_BE_REPO_EQUALS_PATH")
        key, path = value.split("=", 1)
        require(key and path and key not in result, "DUPLICATE_OR_EMPTY_BINDING")
        result[key] = path
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("audit", "rule", "replay"), required=True)
    parser.add_argument("--benchmark", default="data/benchmark.json")
    parser.add_argument("--gt-sidecar", default="data/ground_truth.strict.v1.json")
    parser.add_argument("--repo", action="append", default=[])
    parser.add_argument("--index", action="append", default=[])
    parser.add_argument("--rankings")
    parser.add_argument("--stage", choices=("rule", "rerank"), default="rule")
    parser.add_argument("--candidate-k", type=int, default=5)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        repos, indexes = bindings(args.repo), bindings(args.index)
        report = run_evaluation(args.benchmark, args.gt_sidecar, repos, indexes, args.mode,
                                 args.rankings, args.stage, args.candidate_k, os.environ.get("TASK003_RUNTIME_ROOT"))
        protected = [args.benchmark, args.gt_sidecar, *indexes.values(),
                     *(p + ".meta.json" for p in indexes.values())]
        if args.rankings:
            protected.append(args.rankings)
        write_report(args.out, report, protected, repos.values())
        print("REPORT_WRITTEN " + str(Path(args.out).absolute()))
        print(json.dumps(dict(tool_status=report["tool_status"], evaluation_status=report["evaluation_status"],
                              denominators=report["denominators"]), sort_keys=True))
        return report["exit_code"]
    except (OSError, ValueError, UnicodeError) as exc:
        print("OUTPUT_OR_BINDING_ERROR: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
