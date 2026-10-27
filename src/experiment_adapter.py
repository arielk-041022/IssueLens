"""EXP001-B1: GT-free prediction capabilities, sealed artifacts, separate strict evaluation.

No Rule/BM25/LLM producer is implemented here. External Python producers run only
on synthetic case IDs in this release. The audit hook restricts ordinary Python
I/O; it is not an OS sandbox for hostile native code. See the protocol document.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import traceback

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
K = 5
N = 4
REAL_CASES = {"jsoup-2130", "jsoup-2266", "jsoup-2068", "jsoup-2078"}
FIELDS = ("repo_id", "source_snapshot_id", "file_path", "method_id")
FEATURES = ("name", "class", "signature", "file", "annotations", "strings", "calls")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SE = _load(HERE / "strict_evaluation.py", "experiment_existing_strict")


class AdapterError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise AdapterError(reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def read(path):
    return SE.read_json(path)


def put(path, obj):
    raw = SE.json_bytes(obj)
    with Path(path).open("xb") as handle:
        handle.write(raw)
    return digest(raw)


def fresh(path, roots=()):
    path = Path(path).absolute()
    resolved = path.resolve(strict=False)
    for root in (PROJECT, *roots):
        root = Path(root).resolve(strict=False)
        require(resolved != root and root not in resolved.parents, "OUTPUT_INSIDE_PROTECTED_ROOT")
    require(not path.exists() and not path.is_symlink(), "OUTPUT_ALREADY_EXISTS")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()
    return path


def code_bindings():
    return {name: digest((HERE / name).read_bytes()) for name in
            ("experiment_adapter.py", "strict_evaluation.py", "evidence_validator.py", "retriever.py")}


def error_record(stage, exc):
    return dict(stage=stage, reason_code=str(exc) if isinstance(exc, AdapterError) else type(exc).__name__,
                error_type=type(exc).__name__, detail=str(exc))


def _issue(input_path, case_id):
    document, file_hash = read(input_path)
    require(isinstance(document, list), "INPUT_MUST_BE_FROZEN_ARRAY")
    rows = [r for r in document if isinstance(r, dict) and r.get("id") == case_id]
    require(len(rows) == 1, "CASE_MISSING_OR_DUPLICATE")
    row = rows[0]
    require(set(row) <= {"id", "issue_text", "repo_id", "repo_path", "source_snapshot_id", "issue_input"},
            "GT_OR_UNKNOWN_FIELDS_IN_PREDICTION_INPUT")
    require(SE.text(row.get("repo_id")) and SE.text(row.get("source_snapshot_id")), "MISSING_INPUT_IDENTITY")
    inp = row.get("issue_input")
    require(isinstance(inp, dict), "MISSING_FROZEN_INPUT_CONTRACT")
    allowed = {"title", "title_sha256", "original_body", "body_sha256", "body_bytes", "issue_text_sha256",
               "issue_text_bytes", "contract", "separator", "encoding", "creation_time_version_verified",
               "archived_at_utc", "issue_url", "source_reference", "exclude_from_query"}
    require(set(inp) <= allowed, "GT_OR_UNKNOWN_FIELDS_IN_INPUT_METADATA")
    require(inp.get("contract") == "TITLE_LF_LF_ORIGINAL_BODY_V1", "UNSUPPORTED_INPUT_CONTRACT")
    require(isinstance(inp.get("title"), str) and isinstance(inp.get("original_body"), str), "INVALID_INPUT_TEXT")
    title, body = inp["title"].encode("utf-8"), inp["original_body"].encode("utf-8")
    query = title + b"\n\n" + body
    require(row.get("issue_text", "").encode("utf-8") == query, "ISSUE_INPUT_BYTES_MISMATCH")
    require(inp.get("title_sha256") == digest(title) and inp.get("body_sha256") == digest(body)
            and inp.get("issue_text_sha256") == digest(query), "ISSUE_INPUT_HASH_MISMATCH")
    require(inp.get("body_bytes") == len(body) and inp.get("issue_text_bytes") == len(query), "ISSUE_BYTE_COUNT_MISMATCH")
    return row, file_hash


def make_catalog(index, repo_id, sid):
    SE.build_method_catalog(index, repo_id, sid)
    catalog = []
    for f in index:
        for cls in f["classes"]:
            for m in cls["methods"]:
                require(m.get("location_status") == "AVAILABLE", "METHOD_LOCATION_UNAVAILABLE")
                require(type(m.get("has_body")) is bool, "INVALID_HAS_BODY")
                features = {key: copy.deepcopy(m.get(key)) for key in ("name", "signature", "annotations", "strings", "calls")}
                features.update(file=f["file"], **{"class": cls["name"]})
                record = SE.identity(repo_id, sid, f["file"], m["method_id"])
                record.update(ordinal=len(catalog), has_body=m["has_body"], features=features)
                catalog.append(record)
    return catalog


def prepare(input_path, index_path, repo, case_id, out_dir):
    """GT-free preparation: no sidecar parameter, no GT load, no prediction."""
    out = fresh(out_dir, [repo])
    try:
        row, input_hash = _issue(input_path, case_id)
        index, index_hash = read(index_path)
        meta, meta_hash = read(str(index_path) + ".meta.json")
        require(isinstance(index, list) and meta.get("schema_version") == "2.0", "V2_INDEX_REQUIRED")
        manifest = SE.manifest_for(index)
        sid = "sha256:" + SE.manifest_hash(manifest)
        require(row["source_snapshot_id"] == sid, "SOURCE_SNAPSHOT_MISMATCH")
        proof = SE.load_local("evidence_validator").validate(repo, index_path)
        put(out / "source-validation.json", proof)
        require(proof["report_status"] == "COMPLETE" and proof["overall_verdict"] == "PASS", "SOURCE_VALIDATION_FAILED")
        require(proof["inputs"]["index_sha256"] == index_hash and proof["inputs"]["metadata_sha256"] == meta_hash,
                "INDEX_CHANGED_DURING_PREPARATION")
        actual = {p.relative_to(Path(repo).resolve()).as_posix() for p in
                  (Path(repo).resolve() / "src/main/java").rglob("*.java") if p.is_file()}
        require(actual == {e["file_path"] for e in manifest}, "SOURCE_FILE_SET_MISMATCH")
        catalog = make_catalog(index, row["repo_id"], sid)
        request = dict(schema_version="prediction-request-v1", case_id=case_id, repo_id=row["repo_id"],
                       source_snapshot_id=sid, issue_text=row["issue_text"], input_file_sha256=input_hash,
                       issue_text_sha256=digest(row["issue_text"].encode()), index_sha256=index_hash,
                       metadata_sha256=meta_hash, source_manifest_sha256=SE.manifest_hash(manifest),
                       candidate_k=K, catalog=catalog, catalog_sha256=digest(canonical(catalog)),
                       code_sha256=code_bindings())
        validate_request(request)
        request_hash = put(out / "request.json", request)
        put(out / "package.json", dict(schema_version="prediction-package-v1", request_sha256=request_hash,
                                       case_id=case_id, candidate_count=len(catalog), protected_repo_root=str(Path(repo).resolve())))
        return dict(status="PREPARED", request_sha256=request_hash, candidate_count=len(catalog))
    except Exception as exc:
        result = dict(status="INVALID_INPUT", error=error_record("PREPARE", exc))
        put(out / "prepare-error.json", result)
        return result


def validate_request(request):
    expected = {"schema_version", "case_id", "repo_id", "source_snapshot_id", "issue_text", "input_file_sha256",
                "issue_text_sha256", "index_sha256", "metadata_sha256", "source_manifest_sha256", "candidate_k",
                "catalog", "catalog_sha256", "code_sha256"}
    require(isinstance(request, dict) and set(request) == expected, "UNKNOWN_OR_GT_REQUEST_FIELD")
    require(request["schema_version"] == "prediction-request-v1" and request["candidate_k"] == K, "INVALID_REQUEST_PROTOCOL")
    require(SE.text(request["case_id"]) and isinstance(request["issue_text"], str), "INVALID_REQUEST_TEXT")
    for key in ("input_file_sha256", "issue_text_sha256", "index_sha256", "metadata_sha256", "source_manifest_sha256", "catalog_sha256"):
        require(SE.is_hash(request[key]), "INVALID_REQUEST_HASH")
    require(request["source_snapshot_id"] == "sha256:" + request["source_manifest_sha256"], "REQUEST_SNAPSHOT_MISMATCH")
    require(digest(request["issue_text"].encode()) == request["issue_text_sha256"], "REQUEST_ISSUE_HASH_MISMATCH")
    require(request["code_sha256"] == code_bindings(), "REQUEST_CODE_CHANGED")
    catalog = request["catalog"]
    require(isinstance(catalog, list) and digest(canonical(catalog)) == request["catalog_sha256"], "CATALOG_HASH_MISMATCH")
    keys = []
    for ordinal, record in enumerate(catalog):
        require(set(record) == set(FIELDS) | {"ordinal", "has_body", "features"}, "UNKNOWN_CATALOG_FIELD")
        key = SE.identity_key(record)
        require(key[:2] == (request["repo_id"], request["source_snapshot_id"]), "CATALOG_SNAPSHOT_MISMATCH")
        require(type(record["ordinal"]) is int and record["ordinal"] == ordinal and type(record["has_body"]) is bool,
                "INVALID_CATALOG_ORDINAL")
        require(isinstance(record["features"], dict) and set(record["features"]) == set(FEATURES), "INVALID_FEATURES")
        require(all(isinstance(record["features"][k], str) for k in ("name", "class", "signature", "file")), "INVALID_FEATURE_TEXT")
        require(record["features"]["file"] == record["file_path"], "FEATURE_FILE_MISMATCH")
        require(all(isinstance(record["features"][k], list) and all(isinstance(v, str) for v in record["features"][k])
                    for k in ("annotations", "strings", "calls")), "INVALID_FEATURE_ARRAY")
        keys.append(key)
    require(len(keys) == len(set(keys)) and len({k[3] for k in keys}) == len(keys), "CATALOG_METHOD_ID_CONFLICT")
    return {key: record for key, record in zip(keys, catalog)}


def load_package(package):
    package = Path(package)
    info, _ = read(package / "package.json")
    request, rh = read(package / "request.json")
    require(info.get("schema_version") == "prediction-package-v1" and info.get("request_sha256") == rh, "REQUEST_FILE_HASH_MISMATCH")
    validate_request(request)
    require(info.get("case_id") == request["case_id"] and info.get("candidate_count") == len(request["catalog"]), "PACKAGE_BINDING_MISMATCH")
    return request, rh, info


def stable_score_order(scored, catalog):
    """Only ordering utility: no retrieval/scoring algorithm is implemented."""
    by_key = {SE.identity_key(r): r for r in catalog}
    require(len(by_key) == len(catalog), "DUPLICATE_CATALOG_IDENTITY")
    require(isinstance(scored, list), "INVALID_SCORE_ARRAY")
    keys = [SE.identity_key(r) for r in scored]
    require(len(set(keys)) == len(keys) and set(keys) == set(by_key), "SCORED_CATALOG_COVERAGE_MISMATCH")
    require(all(type(r.get("score")) in (int, float) and math.isfinite(r["score"]) for r in scored), "INVALID_SCORE")
    return sorted(scored, key=lambda r: (-r["score"], by_key[SE.identity_key(r)]["ordinal"]))


def validate_result(result, request):
    require(isinstance(result, dict) and set(result) <= {"status", "ranking", "rerank", "telemetry"}, "INVALID_PRODUCER_RESULT")
    status = result.get("status")
    require(status in {"SUCCESS", "EMPTY"}, "INVALID_PRODUCER_STATUS")
    catalog = validate_request(request)
    ranking = result.get("ranking")
    keys = SE.validate_order(ranking, catalog, request["repo_id"], request["source_snapshot_id"])
    if status == "EMPTY":
        require(ranking == [], "EMPTY_WITH_RANKING")
    else:
        require(bool(ranking) and set(keys) == set(catalog), "MISSING_METHODS_IN_FULL_PREDICTION")
    require(all(set(r) <= set(FIELDS) | {"score", "detail"} for r in ranking), "UNKNOWN_RANKING_FIELD")
    rerank = result.get("rerank", dict(status="NOT_REQUESTED", ranking=[]))
    require(isinstance(rerank, dict) and set(rerank) == {"status", "ranking"}, "INVALID_RERANK_OBJECT")
    require(rerank["status"] in {"VALID", "FALLBACK", "UNAVAILABLE", "DRY_RUN", "NOT_REQUESTED"}, "INVALID_RERANK_STATUS")
    prefix = keys[:K]
    new = SE.validate_order(rerank["ranking"], catalog, request["repo_id"], request["source_snapshot_id"])
    if rerank["status"] == "VALID":
        require(len(new) == len(prefix) and set(new) == set(prefix), "INVALID_RERANK_PERMUTATION")
    else:
        require(new == [] or new == prefix, "FALLBACK_CHANGED_ORDER")
    telemetry = result.get("telemetry", {})
    require(isinstance(telemetry, dict), "INVALID_TELEMETRY")
    if status == "EMPTY":
        require(not telemetry.get("exceptions"), "EMPTY_MUST_NOT_HIDE_REPORTED_EXCEPTION")
    return dict(status=status, ranking=ranking, rerank=rerank,
                telemetry={"usage": {"status": "UNKNOWN"}, "attempts": [], "requests": [], "exceptions": [], **telemetry})


def _install_worker_guard(package, producer):
    allowed_files = {(Path(package) / "request.json").resolve(), Path(producer).resolve()}
    libraries = {Path(sysconfig.get_path(k)).resolve() for k in ("stdlib", "platstdlib")}
    def permitted(path):
        path = Path(os.fsdecode(path)).resolve(strict=False)
        return path in allowed_files or any(root == path or root in path.parents for root in libraries)
    def audit(event, args):
        if event == "open":
            path, mode, flags = args
            if isinstance(path, int):
                if path in (0, 1, 2): return
                raise PermissionError("FILE_DESCRIPTOR_OUTSIDE_CAPABILITY")
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
            if writing or not permitted(path):
                raise PermissionError("READ_OR_WRITE_OUTSIDE_PREDICTION_CAPABILITY: " + str(path))
        elif event.startswith("socket.") or event in {"subprocess.Popen", "os.system", "os.exec", "ctypes.dlopen", "os.chdir"}:
            raise PermissionError("EXECUTION_OR_NETWORK_OUTSIDE_CAPABILITY")
        elif event in {"os.listdir", "os.scandir"}:
            if not permitted(args[0]): raise PermissionError("DIRECTORY_OUTSIDE_CAPABILITY")
    sys.addaudithook(audit)


def _worker(package, producer, authorization_path=None):
    request, _ = read(Path(package) / "request.json")
    validate_request(request)
    authorization = None
    if authorization_path is None:
        require(request["case_id"] not in REAL_CASES and request["case_id"].startswith("fixture-"), "B1_SYNTHETIC_PRODUCER_ONLY")
        require(_load(HERE / "controlled_authorization.py", "legacy_worker_scope").synthetic_request(request), "REAL_INPUT_RENAMING")
    else:
        authority = _load(HERE / "controlled_authorization.py", "worker_authority")
        authorization = authority.claim(read(authorization_path)[0], request, producer, package, "worker")
    _install_worker_guard(package, producer)
    try:
        module = _load(producer, "isolated_synthetic_producer")
        result = module.predict(copy.deepcopy(request)) if authorization is None else module.predict(copy.deepcopy(request), authorization=authorization)
        sys.stdout.buffer.write(SE.json_bytes(result))
        return 0
    except BaseException as exc:
        sys.stdout.buffer.write(SE.json_bytes(dict(worker_error=error_record("PRODUCER", exc), traceback=traceback.format_exc())))
        return 3


def predict(package, producer_path, out_dir, timeout=30, *, authorization=None):
    """Run a GT-free capability worker. B1 producers are synthetic fixtures only."""
    roots = [package]
    try:
        hint, _ = read(Path(package) / "package.json")
        if isinstance(hint.get("protected_repo_root"), str): roots.append(hint["protected_repo_root"])
    except (OSError, ValueError, AttributeError):
        pass  # The actual parse error is recorded below; no prediction is attempted.
    out = fresh(out_dir, roots)
    artifact = dict(schema_version="prediction-v1", prediction_status="INVALID_PREDICTION", error=None)
    try:
        require(type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0, "INVALID_TIMEOUT")
        request, rh, info = load_package(package)
        if authorization is None:
            require(request["case_id"] not in REAL_CASES and request["case_id"].startswith("fixture-"), "B1_REAL_PREDICTION_DISABLED")
            require(_load(HERE / "controlled_authorization.py", "legacy_parent_scope").synthetic_request(request), "REAL_INPUT_RENAMING")
        producer = Path(producer_path).resolve(strict=True)
        require(producer.suffix == ".py", "PYTHON_PRODUCER_REQUIRED")
        if authorization is not None:
            authority = _load(HERE / "controlled_authorization.py", "parent_authority")
            artifact["authorization_binding"] = authority.claim(authorization, request, producer, out, "parent")
        request_raw = (Path(package) / "request.json").read_bytes()
        require(digest(request_raw) == rh, "REQUEST_CHANGED_DURING_READ")
        with (out / "request.json").open("xb") as handle: handle.write(request_raw)
        producer_raw = producer.read_bytes()
        with (out / "producer.py").open("xb") as handle: handle.write(producer_raw)
        artifact.update(case_id=request["case_id"], request_sha256=rh, candidate_count=len(request["catalog"]),
                        input_file_sha256=request["input_file_sha256"], issue_text_sha256=request["issue_text_sha256"],
                        catalog_sha256=request["catalog_sha256"], index_sha256=request["index_sha256"],
                        metadata_sha256=request["metadata_sha256"], source_snapshot_id=request["source_snapshot_id"],
                        repo_id=request["repo_id"], producer_code_sha256=digest(producer_raw),
                        code_sha256=code_bindings(), candidate_k=K)
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH") if key in os.environ}
        env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", TEMP=str(out), TMP=str(out))
        cmd = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "_worker", str(out.resolve()), str((out / "producer.py").resolve())]
        if authorization is not None:
            put(out / "authorization-ticket.json", authorization)
            cmd.append(str((out / "authorization-ticket.json").resolve()))
        put(out / "invocation.json", dict(argv=cmd, timeout_seconds=timeout, api_secrets_forwarded=False))
        try:
            process = subprocess.run(cmd, cwd=out, env=env, capture_output=True, timeout=timeout)
            stdout, stderr, returncode = process.stdout, process.stderr, process.returncode
        except subprocess.TimeoutExpired as exc:
            stdout, stderr = exc.stdout or b"", exc.stderr or b""
            (out / "worker.stdout.log").write_bytes(stdout)
            (out / "worker.stderr.log").write_bytes(stderr)
            artifact.update(prediction_status="EXECUTION_ERROR", error=dict(stage="WORKER", reason_code="WORKER_TIMEOUT", detail=str(exc)))
            return _seal_prediction(out, artifact)
        (out / "worker.stdout.log").write_bytes(stdout)
        (out / "worker.stderr.log").write_bytes(stderr)
        artifact["worker_exit_code"] = returncode
        if returncode != 0:
            artifact.update(prediction_status="EXECUTION_ERROR", error=dict(stage="WORKER", reason_code="WORKER_FAILED", detail=stdout.decode("utf-8", "replace")))
        else:
            raw, _ = read(out / "worker.stdout.log")
            result = validate_result(raw, request)
            artifact.update(prediction_status=result.pop("status"), **result)
    except KeyboardInterrupt as exc:
        artifact.update(prediction_status="EXECUTION_ERROR", error=error_record("EXECUTION_INTERRUPTED", exc))
    except Exception as exc:
        artifact.update(error=error_record("PREDICTION_VALIDATION", exc))
    return _seal_prediction(out, artifact)


def _seal_prediction(out, artifact):
    h = put(out / "prediction.json", artifact)
    receipt = dict(schema_version="prediction-receipt-v1", prediction_sha256=h,
                   prediction_status=artifact["prediction_status"], case_id=artifact.get("case_id"),
                   request_sha256=artifact.get("request_sha256"))
    put(out / "receipt.json", receipt)
    return receipt


def _read_prediction(entry, row, sidecar, audit):
    directory = Path(entry["prediction_dir"])
    artifact, ah = read(directory / "prediction.json")
    receipt, _ = read(directory / "receipt.json")
    require(SE.is_hash(entry.get("prediction_sha256")) and ah == entry["prediction_sha256"] == receipt.get("prediction_sha256"),
            "PREDICTION_FILE_HASH_MISMATCH")
    require(artifact.get("schema_version") == "prediction-v1" and receipt.get("schema_version") == "prediction-receipt-v1", "INVALID_PREDICTION_SCHEMA")
    require(artifact.get("case_id") == row["id"] == receipt.get("case_id"), "PREDICTION_CASE_MISMATCH")
    require(artifact.get("prediction_status") == receipt.get("prediction_status"), "PREDICTION_STATUS_MISMATCH")
    require(artifact.get("prediction_status") in {"SUCCESS", "EMPTY"}, "PREDICTION_NOT_COMPLETED: " + str(artifact.get("prediction_status")))
    request, rh = read(directory / "request.json")
    require(rh == artifact.get("request_sha256") == receipt.get("request_sha256"), "PREDICTION_REQUEST_HASH_MISMATCH")
    validate_request(request)
    require(request["case_id"] == row["id"] and request["issue_text"] == row["issue_text"], "REQUEST_INPUT_MISMATCH")
    snapshot = sidecar["snapshots"][0]
    require(len(sidecar["snapshots"]) == 1, "ONE_SNAPSHOT_PER_CASE_REQUIRED")
    for key, value in (("input_file_sha256", audit["inputs"]["benchmark_sha256"]),
                       ("source_snapshot_id", snapshot["source_snapshot_id"]), ("repo_id", snapshot["repo_id"]),
                       ("index_sha256", snapshot["index_sha256"]), ("metadata_sha256", snapshot["metadata_sha256"]),
                       ("catalog_sha256", request["catalog_sha256"])):
        require(artifact.get(key) == request.get(key) == value, "PREDICTION_BINDING_MISMATCH: " + key)
    require(artifact.get("candidate_count") == len(request["catalog"]) and artifact.get("candidate_k") == K, "PREDICTION_CATALOG_COUNT_MISMATCH")
    require(artifact.get("code_sha256") == code_bindings() and SE.is_hash(artifact.get("producer_code_sha256")), "PRODUCER_CODE_BINDING_MISSING")
    require(digest((directory / "producer.py").read_bytes()) == artifact["producer_code_sha256"], "PRODUCER_SOURCE_HASH_MISMATCH")
    actual_index, actual_ih = read(entry["index"])
    require(actual_ih == snapshot["index_sha256"], "INDEX_CHANGED_DURING_REPLAY")
    require(request["catalog"] == make_catalog(actual_index, snapshot["repo_id"], snapshot["source_snapshot_id"]),
            "REQUEST_CATALOG_DIFFERS_FROM_INDEX")
    result = validate_result(dict(status=artifact["prediction_status"], ranking=artifact.get("ranking"),
                                 rerank=artifact.get("rerank"), telemetry=artifact.get("telemetry")), request)
    return artifact, result


def summarize(rows, denominators, mrr_name, invalid):
    """Keep cohort metric denominators at four, but report actual GT eligibility."""
    metrics = SE.summarize_cases(rows, denominators, "replay", mrr_name, invalid)
    cm = sum(bool(r.get("method_eligible")) for r in rows)
    cf = sum(bool(r.get("file_eligible")) for r in rows)
    metrics.update(method_coverage=dict(numerator=cm, denominator=N, value=cm/N),
                   file_coverage=dict(numerator=cf, denominator=N, value=cf/N), method_not_evaluable_cases=N-cm)
    return metrics


def aggregate(job_path, out_dir, preflight_only=False):
    """Only this independent stage can read GT. Never invokes a producer/scorer."""
    try:
        job, job_hash = read(job_path)
    except Exception as exc:
        out = fresh(out_dir)
        rows = [dict(case_id=None, slot=i, annotation_status="UNKNOWN", method_metrics=None, file_metrics=None) for i in range(N)]
        result = dict(schema_version="experiment-aggregate-v1", mode="preflight" if preflight_only else "evaluate", report_status="COMPLETE", tool_status="INVALID_RUN",
                      evaluation_status="INVALID_RUN", exit_code=2, denominators=dict(total=N, method=N, file=N, frozen_before_ranking=True),
                      cases=rows, issues=[error_record("COHORT_INPUT", exc)])
        result["metrics"] = summarize(rows, result["denominators"], "MRR", True)
        put(out / "aggregate.json", result)
        return result
    entries_hint = job.get("cases", []) if isinstance(job, dict) else []
    if not isinstance(entries_hint, list): entries_hint = []
    roots = [e["repo"] for e in entries_hint if isinstance(e, dict) and isinstance(e.get("repo"), str)]
    out = fresh(out_dir, roots)
    report = dict(schema_version="experiment-aggregate-v1", mode="preflight" if preflight_only else "evaluate", report_status="COMPLETE", tool_status="INVALID_RUN",
                  evaluation_status="INVALID_RUN", exit_code=2, cohort_sha256=job_hash,
                  denominators=dict(total=N, method=N, file=N, frozen_before_ranking=True), cases=[], issues=[],
                  protocol=dict(candidate_k=K, held_out_test=False, cohort_tuning=False, llm_only="NOT_IMPLEMENTED",
                                bm25_specification="PENDING_SEPARATE_REVIEW"))
    try:
        require(isinstance(job, dict) and job.get("schema_version") == "evaluation-cohort-v1", "INVALID_COHORT_SCHEMA")
        ids = job.get("case_ids")
        require(isinstance(ids, list) and len(ids) == N and len(set(ids)) == N and all(SE.text(i) for i in ids), "FIXED_FOUR_CASES_REQUIRED")
        require(set(ids) == REAL_CASES or all(cid.startswith("fixture-") for cid in ids), "UNKNOWN_OR_MIXED_COHORT_IDS")
        require(job.get("candidate_k") == K and job.get("stage") in {"rule", "rerank"}, "INVALID_COHORT_PROTOCOL")
        entries = job.get("cases")
        require(isinstance(entries, list) and all(isinstance(e, dict) and SE.text(e.get("case_id")) for e in entries), "INVALID_COHORT_CASES")
        require(len({e["case_id"] for e in entries}) == len(entries) and all(e["case_id"] in ids for e in entries), "DUPLICATE_OR_UNKNOWN_COHORT_CASE")
        by_id = {e["case_id"]: e for e in entries}
        contexts = {}
        # Finish ALL eligibility audits before opening ANY prediction file.
        for i, cid in enumerate(ids):
            row = dict(case_id=cid, annotation_status="UNKNOWN", method_eligible=False, file_eligible=False, method_metrics=None, file_metrics=None,
                       ranking_status="NOT_EVALUATED", prediction_status="NOT_READ")
            report["cases"].append(row)
            if cid not in by_id:
                row.update(ranking_status="MISSING_CASE")
                report["issues"].append(dict(case_id=cid, stage="PREFLIGHT", reason_code="MISSING_CASE")); continue
            try:
                e = by_id[cid]
                bench, bh = read(e["benchmark"]); side, sh = read(e["gt_sidecar"])
                _issue(e["benchmark"], cid)
                require(len(bench) == len(side["cases"]) == len(side["snapshots"]) == 1 and bench[0]["id"] == cid, "ONE_CASE_PER_SNAPSHOT_REQUIRED")
                repo_id = side["cases"][0]["repo_id"]
                proof = SE.run_evaluation(e["benchmark"], e["gt_sidecar"], {repo_id: e["repo"]}, {repo_id: e["index"]}, mode="audit")
                put(out / (str(i) + ".audit.json"), proof)
                if proof["cases"]:
                    row["annotation_status"] = proof["cases"][0]["annotation_status"]
                    row["method_eligible"] = proof["cases"][0]["method_eligible"]
                    row["file_eligible"] = proof["cases"][0]["file_eligible"]
                require(proof["tool_status"] == "OK" and proof["denominators"]["method"] == proof["denominators"]["file"] == 1, "CASE_NOT_STRICTLY_ELIGIBLE")
                row.update(method_eligible=True, file_eligible=True)
                contexts[cid] = (e, bench[0], side, proof, repo_id)
            except Exception as exc:
                row.update(ranking_status="PREFLIGHT_FAILED", error=error_record("PREFLIGHT", exc))
                report["issues"].append(dict(case_id=cid, **error_record("PREFLIGHT", exc)))
        if not report["issues"] and not preflight_only:
            for i, row in enumerate(report["cases"]):
                cid = row["case_id"]; e, bench, side, proof, repo_id = contexts[cid]
                try:
                    artifact, result = _read_prediction(e, bench, side, proof)
                    row["prediction_status"] = artifact["prediction_status"]
                    gt = side["cases"][0]
                    targets = [SE.identity_key(SE.identity(repo_id, gt["source_snapshot_id"], t["file_path"], t["method_id"]))
                               for t in gt["accepted_targets"]]
                    row["first_stage_target_in_top5"] = any(SE.identity_key(r) in targets for r in result["ranking"][:K])
                    if job["stage"] == "rerank" and result["rerank"]["status"] == "VALID":
                        fp = SE.evaluate_order([SE.identity_key(r) for r in result["rerank"]["ranking"]], targets, gt["accepted_files"])
                        row["fixed_pool_method_metrics"] = {key: fp[key] for key in
                            ("strict_method_top1", "strict_method_top5", "first_strict_rank", "reciprocal_rank", "required_group_coverage_at5")}
                        row["fixed_pool_file_metrics"] = {"strict_file_top1": fp["strict_file_top1"]}
                    # GT hash is attached ONLY here, after predictions are sealed.
                    converted = SE._artifact_header(proof, side, [], K)
                    converted.update(producer_code_sha256=artifact["producer_code_sha256"],
                                     raw_prediction_sha256=e["prediction_sha256"], producer="GT_ISOLATED_EXTERNAL",
                                     scoring_code_hash_role="UNCHANGED_RETRIEVER_COMPATIBILITY_ANCHOR")
                    converted["cases"] = [dict(case_id=cid, repo_id=repo_id, source_snapshot_id=side["cases"][0]["source_snapshot_id"],
                        retrieval_ranking=result["ranking"], candidate_k=K, rerank=result["rerank"])]
                    converted_path = out / (str(i) + ".strict-ranking.json")
                    put(converted_path, converted)
                    evaluated = SE.run_evaluation(e["benchmark"], e["gt_sidecar"], {repo_id: e["repo"]}, {repo_id: e["index"]},
                                                  mode="replay", rankings=converted_path, stage=job["stage"])
                    put(out / (str(i) + ".evaluated.json"), evaluated)
                    row.update(evaluated["cases"][0])
                    require(evaluated["tool_status"] == "OK", "STRICT_REPLAY_FAILED")
                except Exception as exc:
                    row.update(ranking_status="INVALID_PREDICTION", method_metrics=None, file_metrics=None,
                               error=error_record("EVALUATE", exc))
                    report["issues"].append(dict(case_id=cid, **error_record("EVALUATE", exc)))
        elif report["issues"]:
            for row in report["cases"]:
                if row["ranking_status"] == "NOT_EVALUATED": row["ranking_status"] = "NOT_EVALUATED_PREFLIGHT_FAILURE"
        else:
            for row in report["cases"]:
                row["ranking_status"] = "NOT_REQUESTED"
        report["eligible_method_cases"] = sum(r["method_eligible"] for r in report["cases"])
        report["eligible_file_cases"] = sum(r["file_eligible"] for r in report["cases"])
        if not report["issues"]:
            report.update(tool_status="OK", evaluation_status="NOT_EVALUABLE" if preflight_only else "EVALUATED", exit_code=0)
    except Exception as exc:
        report["issues"].append(error_record("COHORT_INPUT", exc))
        if not report["cases"]:
            supplied = job.get("case_ids", []) if isinstance(job, dict) else []
            report["cases"] = [dict(case_id=supplied[i] if isinstance(supplied, list) and i < len(supplied) else None, annotation_status="UNKNOWN",
                                    slot=i, method_metrics=None, file_metrics=None) for i in range(N)]
    report["metrics"] = summarize(report["cases"], report["denominators"], "MRR", bool(report["issues"]))
    recall_ready = len(report["cases"]) == N and all(type(r.get("first_stage_target_in_top5")) is bool for r in report["cases"])
    report["first_stage_recall_at5"] = dict(status="EVALUATED" if recall_ready else "NOT_EVALUABLE", denominator=N,
        numerator=sum(r["first_stage_target_in_top5"] for r in report["cases"]) if recall_ready else None,
        value=sum(r["first_stage_target_in_top5"] for r in report["cases"]) / N if recall_ready else None)
    if isinstance(job, dict) and job.get("stage") == "rerank":
        fixed_rows = [{**r, "method_metrics": r.get("fixed_pool_method_metrics"), "file_metrics": r.get("fixed_pool_file_metrics")}
                      for r in report["cases"]]
        report["fixed_pool_rerank"] = summarize(fixed_rows, report["denominators"], "MRR@5", bool(report["issues"]))
    else:
        report["fixed_pool_rerank"] = dict(status="NOT_REQUESTED", candidate_k=K)
    put(out / "aggregate.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    for key in ("input", "index", "repo", "case-id", "out-dir"): p.add_argument("--" + key, required=True)
    p = sub.add_parser("predict")
    for key in ("package", "producer", "out-dir"): p.add_argument("--" + key, required=True)
    p.add_argument("--timeout", type=float, default=30)
    for command in ("preflight", "evaluate"):
        p = sub.add_parser(command)
        p.add_argument("--cohort", required=True); p.add_argument("--out-dir", required=True)
    p = sub.add_parser("_worker")
    p.add_argument("package"); p.add_argument("producer")
    p.add_argument("authorization", nargs="?")
    a = parser.parse_args(argv)
    try:
        if a.command == "_worker": return _worker(a.package, a.producer, a.authorization)
        if a.command == "prepare": result = prepare(a.input, a.index, a.repo, a.case_id, a.out_dir); ok = result["status"] == "PREPARED"
        elif a.command == "predict": result = predict(a.package, a.producer, a.out_dir, a.timeout); ok = result["prediction_status"] in {"SUCCESS", "EMPTY"}
        else: result = aggregate(a.cohort, a.out_dir, a.command == "preflight"); ok = result["exit_code"] == 0
        print(json.dumps(result if a.command not in {"preflight", "evaluate"} else {k: result[k] for k in ("tool_status", "evaluation_status", "denominators")}, ensure_ascii=False))
        return 0 if ok else 2
    except Exception as exc:
        print("ADAPTER_ERROR: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
