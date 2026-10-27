"""EXP001-B3: exact-source Rule/BM25 transport through the unchanged B1 worker.

No copied/reimplemented scoring functions. Temporary capsules carry the complete
unmodified source bytes; Jieba is imported from the interpreter, never bundled.
Real cases remain disabled. See the protocol and ordinary-user acceptance gate.
"""
import argparse
import base64
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys
import time
import traceback
import types

ROOT = Path(__file__).resolve().parents[1]
IDENTITY = ("repo_id", "source_snapshot_id", "file_path", "method_id")
FEATURES = ("name", "class", "signature", "file", "annotations", "strings", "calls")
AUTHORIZATION_CODE_SHA256 = "d2b4cbd27fa72759434faf8c3705fe1c38cf544917f2f7919ddb3199fca80cab"
REAL_SCOPE_SNAPSHOTS = ['sha256:8bc6a817c5be85301cb443bb13c1f399821417f4188b2a332f4ee1dc76ad49d5', 'sha256:9b4e5d854e6c9b6824972fc0dbdef11af2e7bf541805d51b9267ce7eb6f72300', 'sha256:dfa01854c9992baa5dd7aa7dc7eefc1f4cc49eb03389569113b6a5327ea7c74b', 'sha256:eca1ac73179f98c878c8f655be75ce153096456a852f97d006ee03e0b677c4f3']
REAL_SCOPE_QUERY_HASHES = ['0b8342d3c4ac9046595ff47621b3cf16735371952ae5eeadff900a1bcc34799d', '57f7170c9a4c898a11d64fec1d7a1a79fe67acd2483ab98cdf802eb56317aedb', '63df915e19cfb60221068f36baabf85da529ee76dd602de1169e8444e84347d7', 'db59626a6b115b567e2c5b9d3ba50b160a91e4101a43a02b53edd5168feff00b']
SOURCES = {
    "rule": ("retriever.py", "022f0ac8f0121b99ae0a04ff3a50bbd6c6d20eb22c390f338f27b49e2a54fa51"),
    "bm25": ("bm25_baseline.py", "727a792f4591cd681d7e0ed125d7e242bcf9335bb3641480b03cfb30ef615be6"),
}


class ProducerError(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise ProducerError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def hash_value(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def validate_worker_request(request, *, authorization=None, system=None):
    """Read-free defensive contract; B1 separately validates current code/source."""
    fields = {"schema_version", "case_id", "repo_id", "source_snapshot_id", "issue_text",
              "input_file_sha256", "issue_text_sha256", "index_sha256", "metadata_sha256",
              "source_manifest_sha256", "candidate_k", "catalog", "catalog_sha256", "code_sha256"}
    require(isinstance(request, dict) and set(request) == fields, "INVALID_OR_GT_REQUEST_FIELDS")
    require(request["schema_version"] == "prediction-request-v1", "INVALID_REQUEST_SCHEMA")
    if authorization is None:
        require(isinstance(request["case_id"], str) and request["case_id"].startswith("fixture-"), "B3_REAL_CASE_DISABLED")
        require(request["repo_id"] != "jhy/jsoup" and request["source_snapshot_id"] not in REAL_SCOPE_SNAPSHOTS
                and request["issue_text_sha256"] not in REAL_SCOPE_QUERY_HASHES
                and not any(r.get("file_path", "").startswith("src/main/java/org/jsoup/") for r in request["catalog"]), "REAL_INPUT_RENAMING")
    else:
        source = authorization.get("verifier_source") if isinstance(authorization, dict) else None
        require(isinstance(source, str) and sha(source.encode()) == AUTHORIZATION_CODE_SHA256, "UNTRUSTED_AUTHORIZATION_CODE")
        verifier = source_module(source.encode(), "bridge_authority", "controlled_authorization.py")
        verifier.validate_context(authorization, request, "Rule-only" if system == "rule" else "BM25-only")
    require(type(request["candidate_k"]) is int and request["candidate_k"] == 5, "INVALID_K")
    require(isinstance(request["repo_id"], str) and bool(request["repo_id"].strip())
            and isinstance(request["issue_text"], str), "INVALID_TEXT")
    for field in ("input_file_sha256", "issue_text_sha256", "index_sha256", "metadata_sha256",
                  "source_manifest_sha256", "catalog_sha256"):
        require(hash_value(request[field]), "INVALID_REQUEST_HASH")
    require(request["source_snapshot_id"] == "sha256:" + request["source_manifest_sha256"], "SNAPSHOT_MISMATCH")
    require(sha(request["issue_text"].encode("utf-8")) == request["issue_text_sha256"], "ISSUE_HASH_MISMATCH")
    code = request["code_sha256"]
    require(isinstance(code, dict) and set(code) == {"experiment_adapter.py", "retriever.py",
            "strict_evaluation.py", "evidence_validator.py"} and all(hash_value(h) for h in code.values()), "INVALID_CODE_BINDING")
    require(isinstance(request["catalog"], list) and sha(canonical(request["catalog"])) == request["catalog_sha256"], "CATALOG_HASH_MISMATCH")
    seen = set()
    for ordinal, rec in enumerate(request["catalog"]):
        require(isinstance(rec, dict) and set(rec) == set(IDENTITY) | {"ordinal", "has_body", "features"}, "INVALID_CATALOG_FIELDS")
        require(all(isinstance(rec[k], str) and rec[k].strip() for k in IDENTITY), "MISSING_IDENTITY")
        require((rec["repo_id"], rec["source_snapshot_id"]) == (request["repo_id"], request["source_snapshot_id"]), "CROSS_SNAPSHOT_IDENTITY")
        path = rec["file_path"]; parts = path.split("/")
        require("\\" not in path and ":" not in path and parts[:3] == ["src", "main", "java"]
                and len(parts) >= 4 and path.endswith(".java") and all(ord(c) >= 32 for c in path)
                and all(p and p not in {".", ".."} and not p.endswith((".", " ")) for p in parts), "INVALID_PATH")
        mid = rec["method_id"]
        require(mid.startswith("java-source-v1:") and mid not in seen, "INVALID_OR_DUPLICATE_METHOD_ID")
        seen.add(mid)
        require(type(rec["ordinal"]) is int and rec["ordinal"] == ordinal and type(rec["has_body"]) is bool, "INVALID_ORDINAL_OR_BODY_FLAG")
        f = rec["features"]
        require(isinstance(f, dict) and set(f) == set(FEATURES), "INVALID_FEATURE_FIELDS")
        require(all(isinstance(f[k], str) for k in FEATURES[:4]) and f["file"] == path, "INVALID_FEATURE_TEXT")
        require(all(isinstance(f[k], list) and all(isinstance(v, str) for v in f[k]) for k in FEATURES[4:]), "INVALID_FEATURE_ARRAY")


def source_module(raw, name, filename):
    """Execute the exact original bytes, not an AST extraction or rewritten scorer."""
    module = types.ModuleType(name)
    module.__file__ = filename
    exec(compile(raw, filename, "exec"), module.__dict__)
    return module


def predict_embedded(request, system, source_bytes, bridge_sha256, *, worker_cache_directory=None, authorization=None):
    validate_worker_request(request, authorization=authorization, system=system)  # before loading any external dependency
    require(system in SOURCES and hash_value(bridge_sha256), "INVALID_SYSTEM_OR_BRIDGE_HASH")
    filename, expected = SOURCES[system]
    require(isinstance(source_bytes, bytes) and sha(source_bytes) == expected, "SCORER_SOURCE_HASH_MISMATCH")
    if system == "rule":
        require(request["code_sha256"]["retriever.py"] == expected, "RULE_REQUEST_SOURCE_MISMATCH")
    started = time.perf_counter()
    scorer = source_module(source_bytes, "b3_exact_" + system, filename)
    if system == "rule":
        # Same function calls, argument values and stable sort as retriever.main.
        # Do not call main (loads Benchmark) or method_key (legacy identity).
        tokenizer = scorer.jieba.dt
        previous_tmp_dir = tokenizer.tmp_dir
        initialized_before = tokenizer.initialized
        cache_directory = None
        if worker_cache_directory is not None:
            # Supplied by the capsule's actual __file__, never by request/GT.
            # B1 sets cwd to its fresh prediction directory. No mkdir or write.
            cache_directory = Path(worker_cache_directory)
            require(cache_directory.is_absolute() and cache_directory.is_dir()
                    and cache_directory.resolve() == Path.cwd().resolve(), "INVALID_WORKER_CACHE_DIRECTORY")
            # A real writable directory can make Windows tempfile retry audit-
            # denied opens indefinitely. Keep a reserved child deliberately
            # absent: the original Jieba cache attempt then fails promptly.
            cache_directory = cache_directory.resolve() / ".jieba-cache-r1"
            require(not cache_directory.exists() and not cache_directory.is_symlink(), "WORKER_CACHE_SLOT_EXISTS")
            cache_directory = str(cache_directory)
        try:
            if cache_directory is not None:
                # Avoid tempfile's write-based directory discovery. Subsequent
                # cache reads/writes still face the unchanged B1 audit hook.
                tokenizer.tmp_dir = cache_directory
            keywords = scorer.extract_keywords(request["issue_text"])
        finally:
            tokenizer.tmp_dir = previous_tmp_dir
        cache_runtime = dict(directory=cache_directory, initialized_before=initialized_before,
                             initialized_after=tokenizer.initialized, tmp_dir_restored=tokenizer.tmp_dir == previous_tmp_dir,
                             prewarm_outside_worker=False, io_policy="UNCHANGED_B1",
                             policy="R1_UNCREATED_WORKER_CACHE_SLOT" if cache_directory else "ORIGINAL_DIRECT_CALL")
        ranking = []
        for record in request["catalog"]:
            score, detail = scorer.score_method(copy.deepcopy(record["features"]), keywords, request["issue_text"])
            require(type(score) in (int, float) and math.isfinite(score), "INVALID_RULE_SCORE")
            ranking.append({**{k: record[k] for k in IDENTITY}, "score": score, "detail": detail})
        ranking.sort(key=lambda row: row["score"], reverse=True)
        # Original dictionary/HMM/initialization logic; denied cache I/O and its
        # original Jieba handling are retained in worker.stderr.log.
        jieba = scorer.jieba
        with jieba.get_dict_file() as handle:
            dictionary_hash = sha(handle.read())
        dependency = dict(jieba_version=getattr(jieba, "__version__", "UNKNOWN"),
                          jieba_module_sha256=sha(Path(jieba.__file__).read_bytes()),
                          jieba_dictionary_sha256=dictionary_hash)
        result = dict(status="SUCCESS" if ranking else "EMPTY", ranking=ranking,
                      telemetry=dict(keywords=keywords, dependencies=dependency, cache_runtime=cache_runtime))
    else:
        result = scorer.predict(copy.deepcopy(request)) if authorization is None else scorer.predict(copy.deepcopy(request), authorization=authorization)  # frozen math, full catalog
    elapsed = time.perf_counter() - started
    result.setdefault("telemetry", {}).update(
        system="Rule-only" if system == "rule" else "BM25-only",
        producer_id="EXP001-B3:" + system, scorer_source_file=filename,
        scorer_source_sha256=expected, bridge_source_sha256=bridge_sha256,
        catalog_sha256=request["catalog_sha256"], candidate_count=len(request["catalog"]),
        scored_count=len(request["catalog"]), output_count=len(result["ranking"]),
        visible_fields=list(FEATURES), scope="FULL_CATALOG", elapsed_seconds=elapsed,
        python_version=sys.version, usage={"status": "NOT_APPLICABLE_OFFLINE"},
        attempts=[], requests=[], exceptions=[])
    return result


def load_adapter():
    path = ROOT / "src/experiment_adapter.py"
    spec = importlib.util.spec_from_file_location("b3_unchanged_adapter", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def capsule_bytes(system, *, controlled=False):
    require(system in SOURCES, "UNKNOWN_SYSTEM")
    name, expected = SOURCES[system]
    scorer = (ROOT / "src" / name).read_bytes()
    require(sha(scorer) == expected, "SCORER_SOURCE_CHANGED")
    bridge = Path(__file__).read_bytes()
    payload = dict(system=system, bridge=base64.b64encode(bridge).decode("ascii"),
                   bridge_sha256=sha(bridge), scorer=base64.b64encode(scorer).decode("ascii"), scorer_sha256=sha(scorer))
    # The only new transport is encoding. Original scorer bytes are never edited.
    bootstrap = '''import base64, hashlib, types
from pathlib import Path
_PAYLOAD = PAYLOAD_LITERAL
_raw = base64.b64decode(_PAYLOAD['bridge'], validate=True)
if hashlib.sha256(_raw).hexdigest() != _PAYLOAD['bridge_sha256']:
    raise ValueError('BRIDGE_SOURCE_HASH_MISMATCH')
_bridge = types.ModuleType('b3_exact_bridge')
_bridge.__file__ = 'baseline_producers.py'
exec(compile(_raw, _bridge.__file__, 'exec'), _bridge.__dict__)
def predict(request, *, authorization=None):
    if authorization is not None and not CONTROLLED_LITERAL:
        raise ValueError('CONTROLLED_CAPSULE_REQUIRED')
    raw = base64.b64decode(_PAYLOAD['scorer'], validate=True)
    if hashlib.sha256(raw).hexdigest() != _PAYLOAD['scorer_sha256']:
        raise ValueError('SCORER_PAYLOAD_HASH_MISMATCH')
    cache_directory = str(Path(__file__).resolve().parent) if _PAYLOAD['system'] == 'rule' else None
    return _bridge.predict_embedded(request, _PAYLOAD['system'], raw, _PAYLOAD['bridge_sha256'], worker_cache_directory=cache_directory, authorization=authorization)
'''
    return bootstrap.replace("PAYLOAD_LITERAL", repr(payload)).replace("CONTROLLED_LITERAL", repr(controlled)).encode("utf-8"), payload


def run(system, package, out_dir):
    adapter = load_adapter()
    roots = [package]
    try:
        hint, _ = adapter.read(Path(package) / "package.json")
        if isinstance(hint, dict) and isinstance(hint.get("protected_repo_root"), str): roots.append(hint["protected_repo_root"])
    except (OSError, ValueError):
        pass  # The ordinary load below records the actual failure.
    out = adapter.fresh(out_dir, roots)
    try:
        request, request_hash, _ = adapter.load_package(package)
        validate_worker_request(request)
        require(system in SOURCES, "UNKNOWN_SYSTEM")
        raw, payload = capsule_bytes(system)
        with (out / "producer-capsule.py").open("xb") as handle: handle.write(raw)
        adapter.put(out / "transport.json", dict(system=system, request_sha256=request_hash,
            capsule_sha256=sha(raw), bridge_source_sha256=payload["bridge_sha256"],
            scorer_source_sha256=payload["scorer_sha256"], scorer_source_file=SOURCES[system][0],
            dependency_copy=False, source_bytes_edited=False))
        receipt = adapter.predict(package, out / "producer-capsule.py", out / "prediction")
        result = dict(system=system, prediction_dir=str((out / "prediction").resolve()), **receipt)
    except Exception as exc:
        result = dict(system=system, prediction_status="INVALID_PREDICTION",
                      error=dict(stage="B3_TRANSPORT", reason_code=type(exc).__name__, detail=str(exc), traceback=traceback.format_exc()))
    adapter.put(out / "result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system", choices=sorted(SOURCES), required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    try:
        result = run(args.system, args.package, args.out_dir)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["prediction_status"] in {"SUCCESS", "EMPTY"} else 2
    except Exception as exc:
        print("B3_ERROR: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
