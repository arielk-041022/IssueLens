"""Offline one-run authority. Real issuance requires separately pinned approval.

Owner-controlled local HMAC registry, not third-party identity authentication or
an OS sandbox against the registry owner. Development is not real-run approval.
"""
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SYSTEMS = ["Rule-only", "BM25-only"]
REAL_IDS = {"jsoup-2130", "jsoup-2266", "jsoup-2068", "jsoup-2078"}
REAL_SNAPSHOTS = {
    "sha256:dfa01854c9992baa5dd7aa7dc7eefc1f4cc49eb03389569113b6a5327ea7c74b",
    "sha256:eca1ac73179f98c878c8f655be75ce153096456a852f97d006ee03e0b677c4f3",
    "sha256:9b4e5d854e6c9b6824972fc0dbdef11af2e7bf541805d51b9267ce7eb6f72300",
    "sha256:8bc6a817c5be85301cb443bb13c1f399821417f4188b2a332f4ee1dc76ad49d5",
}
REAL_CASE_SNAPSHOTS = dict(zip(["jsoup-2130", "jsoup-2266", "jsoup-2068", "jsoup-2078"], [
    "sha256:dfa01854c9992baa5dd7aa7dc7eefc1f4cc49eb03389569113b6a5327ea7c74b",
    "sha256:eca1ac73179f98c878c8f655be75ce153096456a852f97d006ee03e0b677c4f3",
    "sha256:9b4e5d854e6c9b6824972fc0dbdef11af2e7bf541805d51b9267ce7eb6f72300",
    "sha256:8bc6a817c5be85301cb443bb13c1f399821417f4188b2a332f4ee1dc76ad49d5"]))
REAL_QUERY_HASHES = {"0b8342d3c4ac9046595ff47621b3cf16735371952ae5eeadff900a1bcc34799d",
    "57f7170c9a4c898a11d64fec1d7a1a79fe67acd2483ab98cdf802eb56317aedb",
    "db59626a6b115b567e2c5b9d3ba50b160a91e4101a43a02b53edd5168feff00b",
    "63df915e19cfb60221068f36baabf85da529ee76dd602de1169e8444e84347d7"}
SPEC = {"version": "controlled-offline-v1", "N": 4, "K": 5, "systems": SYSTEMS,
        "prediction_gt": "FORBIDDEN", "real_issuance": "SEPARATE_PINNED_RESEARCH_APPROVAL_REQUIRED",
        "bm25_spec_sha256": "f28889a3671eccef24c63ca507c348cca586bebde0da3cab3702a3f251cdfeda",
        "replay": "one campaign; one parent and worker claim per case/system; failure consumes claim"}
CODE = ("experiment_adapter.py", "baseline_producers.py", "bm25_baseline.py",
        "controlled_authorization.py", "retriever.py", "strict_evaluation.py", "evidence_validator.py")
CASE_FIELDS = {"case_id", "repo_id", "source_snapshot_id", "input", "repo", "index", "package",
               "input_file_sha256", "issue_text_sha256", "index_sha256", "metadata_sha256",
               "source_manifest_sha256", "catalog_sha256", "request_sha256", "request_canonical_sha256", "candidate_count"}


def require(ok, reason):
    if not ok: raise ValueError(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(raw): return hashlib.sha256(raw).hexdigest()
def file_hash(path): return digest(Path(path).read_bytes())


def read(path):
    def pairs(items):
        d = {}
        for k, v in items:
            require(k not in d, "DUPLICATE_JSON_KEY"); d[k] = v
        return d
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=pairs,
                      parse_constant=lambda v: require(False, "NONFINITE_JSON"))


def put(path, value):
    with Path(path).open("xb") as f: f.write(canonical(value))


def sign(key, value): return hmac.new(bytes.fromhex(key), canonical(value), hashlib.sha256).hexdigest()
def runtime(): return {"executable": str(Path(sys.executable).resolve()), "version": sys.version, "executable_sha256": file_hash(sys.executable)}


def load(name):
    s = importlib.util.spec_from_file_location("c1_" + name, ROOT / "src" / (name + ".py"))
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m


def codes(): return {name: file_hash(ROOT / "src" / name) for name in CODE}


def synthetic_request(request):
    return (request["case_id"].startswith("fixture-") and request["repo_id"] != "jhy/jsoup"
            and request["source_snapshot_id"] not in REAL_SNAPSHOTS and request["issue_text_sha256"] not in REAL_QUERY_HASHES
            and not any(r["file_path"].startswith("src/main/java/org/jsoup/") for r in request["catalog"]))


def outside(path, roots=()):
    p = Path(path).resolve()
    require(all(p != Path(r).resolve() and Path(r).resolve() not in p.parents for r in (ROOT, *roots)), "OUTPUT_PROTECTED_ROOT")
    return p


def init_issuer(path):
    path = outside(path)
    if not path.exists():
        path.mkdir(parents=True)
        put(path / "registry.json", {"version": "local-issuer-v1", "issuer_id": secrets.token_hex(16),
                                    "key": secrets.token_hex(32), "real_approval_pins": {}})
    registry = read(path / "registry.json")
    require(set(registry) == {"version", "issuer_id", "key", "real_approval_pins"}, "INVALID_REGISTRY")
    return registry


def run_slot(root, run_id, suffix):
    return Path(root) / (digest(run_id.encode("utf-8")) + "." + suffix)


def qualify_synthetic(issuer, cohort_path, out_dir):
    """Separate GT-reading stage: only sanitized signed eligibility leaves it."""
    job = read(cohort_path)
    roots = [e["repo"] for e in job["cases"]]
    outside(issuer, roots); outside(out_dir, roots)
    registry = init_issuer(issuer)
    ids = job.get("case_ids", [])
    require(len(ids) == len(set(ids)) == 4 and all(isinstance(c, str) and c.startswith("fixture-") for c in ids), "SYNTHETIC_QUALIFICATION_ONLY")
    out = outside(out_dir); require(not out.exists(), "OUTPUT_EXISTS")
    process = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "src/experiment_adapter.py"),
        "preflight", "--cohort", str(Path(cohort_path).resolve()), "--out-dir", str(out)], capture_output=True)
    (out / "preflight.stdout.log").write_bytes(process.stdout); (out / "preflight.stderr.log").write_bytes(process.stderr)
    report = read(out / "aggregate.json")
    require(process.returncode == 0 and report["eligible_method_cases"] == 4 and report["eligible_file_cases"] == 4, "QUALIFICATION_FAILED")
    cases = []
    entries = {e["case_id"]: e for e in job["cases"]}
    for i, cid in enumerate(ids):
        audit = read(out / (str(i) + ".audit.json")); snapshot = audit["snapshot_results"][0]
        cases.append(dict(case_id=cid, repo_id=snapshot["repo_id"], source_snapshot_id=snapshot["source_snapshot_id"],
                          input=str(Path(entries[cid]["benchmark"]).resolve()), repo=str(Path(entries[cid]["repo"]).resolve()),
                          index=str(Path(entries[cid]["index"]).resolve()), input_file_sha256=audit["inputs"]["benchmark_sha256"],
                          index_sha256=snapshot["index_sha256"], metadata_sha256=snapshot["metadata_sha256"]))
    body = dict(version="eligibility-v1", issuer_id=registry["issuer_id"], cases=cases, N=4, K=5)
    receipt = {"body": body, "mac": sign(registry["key"], body)}
    put(out / "eligibility.json", receipt)
    return receipt


def describe(input_path, index, repo, package):
    """GT-free stage, called after qualification; never reads a sidecar."""
    a = load("experiment_adapter"); request, rh, _ = a.load_package(package)
    return {"case_id": request["case_id"], "repo_id": request["repo_id"], "source_snapshot_id": request["source_snapshot_id"],
            "input": str(Path(input_path).resolve()), "index": str(Path(index).resolve()), "repo": str(Path(repo).resolve()),
            "package": str(Path(package).resolve()), "request_sha256": rh, "request_canonical_sha256": digest(canonical(request)), "candidate_count": len(request["catalog"]),
            **{k: request[k] for k in ("input_file_sha256", "issue_text_sha256", "index_sha256", "metadata_sha256", "source_manifest_sha256", "catalog_sha256")}}


def research_approval(issuer, approval_path, purpose, subject_sha256):
    """Read a separately provisioned, issuer-pinned human approval; never create it.

    Registry custody is the same local-owner trust boundary as C1. Neither CLI
    accepts a new approval hash or provides a command to install approval pins.
    """
    registry = read(Path(issuer) / "registry.json")
    anchors = read(Path(issuer) / "research-approval-pins.json")
    approval_hash = file_hash(approval_path)
    require(isinstance(anchors, dict) and anchors.get(purpose) == approval_hash, "RESEARCH_APPROVAL_NOT_PINNED")
    envelope = read(approval_path)
    require(set(envelope) == {"body", "mac"}, "INVALID_RESEARCH_APPROVAL")
    body = envelope["body"]
    require(set(body) == {"version", "issuer_id", "role", "decision", "purpose", "subject_sha256", "record"}, "INVALID_RESEARCH_APPROVAL")
    require(hmac.compare_digest(envelope["mac"], sign(registry["key"], body)), "INVALID_RESEARCH_APPROVAL_SIGNATURE")
    require(body["version"] == "research-approval-v1" and body["issuer_id"] == registry["issuer_id"]
            and body["role"] == "RESEARCH_LEAD" and body["decision"] == "APPROVED"
            and body["purpose"] == purpose and body["subject_sha256"] == subject_sha256
            and isinstance(body["record"], str) and len(body["record"].strip()) >= 20, "RESEARCH_APPROVAL_SCOPE_MISMATCH")
    return registry


def real_runtime():
    require(sys.version_info[:2] == (3, 10), "REAL_PYTHON310_REQUIRED")


def qualify_real(issuer, cohort_path, out_dir, approval_path):
    """GT-reading subprocess stage. Approval binds the full qualification cohort."""
    real_runtime()
    job = read(cohort_path)
    require(len(job.get("case_ids", [])) == 4 and set(job["case_ids"]) == REAL_IDS
            and len(job.get("cases", [])) == 4 and {c["case_id"] for c in job["cases"]} == REAL_IDS
            and job.get("candidate_k") == 5, "INVALID_REAL_COHORT")
    registry = research_approval(issuer, approval_path, "QUALIFY_REAL", file_hash(cohort_path))
    roots = [c["repo"] for c in job["cases"]]
    outside(issuer, roots); out = outside(out_dir, roots)
    require(not out.exists(), "OUTPUT_EXISTS")
    process = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "src/experiment_adapter.py"),
        "preflight", "--cohort", str(Path(cohort_path).resolve()), "--out-dir", str(out)], capture_output=True)
    require(out.is_dir(), "QUALIFICATION_NO_REPORT_DIRECTORY")
    (out / "preflight.stdout.log").write_bytes(process.stdout)
    (out / "preflight.stderr.log").write_bytes(process.stderr)
    report = read(out / "aggregate.json")
    require(process.returncode == 0 and report["eligible_method_cases"] == 4 and report["eligible_file_cases"] == 4, "QUALIFICATION_FAILED")
    entries = {e["case_id"]: e for e in job["cases"]}; cases = []
    for i, cid in enumerate(job["case_ids"]):
        audit = read(out / (str(i) + ".audit.json")); snapshot = audit["snapshot_results"][0]
        require(snapshot["repo_id"] == "jhy/jsoup" and snapshot["source_snapshot_id"] == REAL_CASE_SNAPSHOTS[cid], "REAL_SNAPSHOT_REQUIRED")
        cases.append(dict(case_id=cid, repo_id=snapshot["repo_id"], source_snapshot_id=snapshot["source_snapshot_id"],
            input=str(Path(entries[cid]["benchmark"]).resolve()), repo=str(Path(entries[cid]["repo"]).resolve()),
            index=str(Path(entries[cid]["index"]).resolve()), input_file_sha256=audit["inputs"]["benchmark_sha256"],
            index_sha256=snapshot["index_sha256"], metadata_sha256=snapshot["metadata_sha256"]))
    body = dict(version="eligibility-v1", issuer_id=registry["issuer_id"], cases=cases, N=4, K=5)
    receipt = dict(body=body, mac=sign(registry["key"], body))
    put(out / "eligibility.json", receipt)
    return receipt


def real_body(registry, run_id, eligibility, cases, output):
    """Read-only deterministic proposal for the research lead's exact-body review."""
    require(isinstance(run_id, str) and 1 <= len(run_id) <= 160, "INVALID_RUN_ID")
    require(len(cases) == 4 and {c["case_id"] for c in cases} == REAL_IDS, "FIXED_REAL_CASES_REQUIRED")
    require(all(c["repo_id"] == "jhy/jsoup" and c["source_snapshot_id"] == REAL_CASE_SNAPSHOTS[c["case_id"]] for c in cases), "REAL_SNAPSHOT_REQUIRED")
    require(eligibility["mac"] == sign(registry["key"], eligibility["body"]), "INVALID_ELIGIBILITY")
    return dict(version="permit-v1", scope="REAL_OFFLINE_ONCE", issuer_id=registry["issuer_id"], run_id=run_id,
        cases=cases, systems=SYSTEMS, N=4, K=5, runtime=runtime(), code_sha256=codes(), specification=SPEC,
        specification_sha256=digest(canonical(SPEC)), output=str(outside(output, [p for c in cases for p in (c["repo"], c["package"])])),
        eligibility=eligibility["body"])


def issue_real(issuer, run_id, eligibility, cases, output, permit_path, approval_path):
    real_runtime()
    roots = [p for c in cases for p in (c["repo"], c["package"])]
    outside(issuer, roots); outside(permit_path, roots)
    registry = read(Path(issuer) / "registry.json")
    body = real_body(registry, run_id, eligibility, cases, output)
    body_hash = digest(canonical(body))
    research_approval(issuer, approval_path, "ISSUE_REAL", body_hash)
    permit = dict(body=body, mac=sign(registry["key"], body), issuer_registry=str(Path(issuer).resolve()))
    validate_shape(permit, registry["key"], body_hash)
    require(not Path(output).exists() and not Path(permit_path).exists(), "OUTPUT_EXISTS")
    # Reserve first: any later failure consumes run_id, never grants a retry.
    put(run_slot(issuer, run_id, "issued"), dict(payload_sha256=body_hash))
    pins = dict(registry["real_approval_pins"])
    require(run_id not in pins, "REAL_APPROVAL_ALREADY_REGISTERED")
    pins[run_id] = body_hash
    updated = {**registry, "real_approval_pins": pins}
    pending = Path(issuer) / (digest(run_id.encode()) + ".registry.pending")
    put(pending, updated)
    os.replace(pending, Path(issuer) / "registry.json")
    put(permit_path, permit)
    ph = file_hash(permit_path)
    try:
        verify_disk(dict(permit_path=str(permit_path), permit_sha256=ph, run_id=run_id,
                         case_id=cases[0]["case_id"], system=SYSTEMS[0]))
    except Exception:
        # Revoke the pin before preserving rejected material. run_id is consumed.
        updated["real_approval_pins"].pop(run_id)
        put(pending, updated)
        os.replace(pending, Path(issuer) / "registry.json")
        os.replace(Path(permit_path), Path(str(permit_path) + ".REJECTED"))
        raise
    return ph


def issue_synthetic(issuer, run_id, eligibility, cases, output, permit_path):
    require(isinstance(cases, list) and len(cases) == 4, "FIXED_FOUR_CASES_REQUIRED")
    roots = [p for c in cases for p in (c["repo"], c["package"])]
    outside(issuer, roots); outside(permit_path, roots); outside(output, roots)
    registry = init_issuer(issuer)
    require(isinstance(run_id, str) and 1 <= len(run_id) <= 160, "INVALID_RUN_ID")
    require(eligibility["mac"] == sign(registry["key"], eligibility["body"]), "INVALID_ELIGIBILITY")
    body = dict(version="permit-v1", scope="SYNTHETIC_ONLY", issuer_id=registry["issuer_id"], run_id=run_id,
        cases=cases, systems=SYSTEMS, N=4, K=5, runtime=runtime(), code_sha256=codes(), specification=SPEC,
        specification_sha256=digest(canonical(SPEC)), output=str(outside(output, [p for c in cases for p in (c["repo"], c["package"])])),
        eligibility=eligibility["body"])
    permit = {"body": body, "mac": sign(registry["key"], body), "issuer_registry": str(Path(issuer).resolve())}
    validate_shape(permit, registry["key"], None)
    require(not Path(output).exists(), "OUTPUT_EXISTS")
    # Exclusive issuance reservation survives all later failures. Never reused.
    put(run_slot(issuer, run_id, "issued"), {"payload_sha256": digest(canonical(body))})
    put(permit_path, permit)
    return digest(canonical(permit))


def validate_shape(permit, key, approval_pin):
    require(isinstance(permit, dict) and set(permit) == {"body", "mac", "issuer_registry"}, "INVALID_PERMIT_FIELDS")
    b = permit["body"]
    require(set(b) == {"version", "scope", "issuer_id", "run_id", "cases", "systems", "N", "K", "runtime", "code_sha256",
                      "specification", "specification_sha256", "output", "eligibility"}, "INVALID_PERMIT_BODY")
    require(hmac.compare_digest(permit["mac"], sign(key, b)), "INVALID_PERMIT_SIGNATURE")
    require(b["version"] == "permit-v1" and type(b["N"]) is int and b["N"] == 4 and type(b["K"]) is int and b["K"] == 5
            and b["systems"] == SYSTEMS, "INVALID_EXPERIMENT_SCOPE")
    require(b["specification"] == SPEC and b["specification_sha256"] == digest(canonical(SPEC)), "SPECIFICATION_MISMATCH")
    require(set(b["code_sha256"]) == set(CODE), "CODE_BINDING_FIELDS")
    require(isinstance(b["run_id"], str) and b["run_id"] and Path(b["output"]).is_absolute(), "INVALID_RUN_OR_OUTPUT")
    require(isinstance(b["cases"], list) and len(b["cases"]) == 4 and all(set(c) == CASE_FIELDS for c in b["cases"]), "FIXED_FOUR_CASES_REQUIRED")
    ids = [c["case_id"] for c in b["cases"]]; require(len(set(ids)) == 4, "DUPLICATE_CASE")
    if b["scope"] == "SYNTHETIC_ONLY":
        require(all(cid.startswith("fixture-") for cid in ids), "REAL_CASE_IN_SYNTHETIC_PERMIT")
        require(all(c["repo_id"] != "jhy/jsoup" and c["source_snapshot_id"] not in REAL_SNAPSHOTS for c in b["cases"]), "REAL_SNAPSHOT_RENAMING")
    else:
        require(b["scope"] == "REAL_OFFLINE_ONCE" and set(ids) == REAL_IDS
                and approval_pin == digest(canonical(b)), "REAL_RESEARCH_APPROVAL_ABSENT")
    eligibility = b["eligibility"]
    require(set(eligibility) == {"version", "issuer_id", "cases", "N", "K"} and eligibility["issuer_id"] == b["issuer_id"]
            and eligibility["N"] == 4 and eligibility["K"] == 5 and len(eligibility["cases"]) == 4, "INVALID_ELIGIBILITY_SCOPE")
    for c, e in zip(b["cases"], eligibility["cases"]):
        require(set(e) == {"case_id", "repo_id", "source_snapshot_id", "input", "repo", "index", "input_file_sha256", "index_sha256", "metadata_sha256"}
                and all(c[k] == e[k] for k in e), "QUALIFICATION_BINDING_MISMATCH")


def verify_disk(ticket):
    require(set(ticket) == {"permit_path", "permit_sha256", "run_id", "system", "case_id"}, "INVALID_TICKET_FIELDS")
    require(file_hash(ticket["permit_path"]) == ticket["permit_sha256"], "PERMIT_FILE_HASH_MISMATCH")
    permit = read(ticket["permit_path"]); registry = read(Path(permit["issuer_registry"]) / "registry.json")
    b = permit["body"]; pin = registry["real_approval_pins"].get(b["run_id"])
    validate_shape(permit, registry["key"], pin)
    require(b["issuer_id"] == registry["issuer_id"] and b["run_id"] == ticket["run_id"], "RUN_ID_MISMATCH")
    require(ticket["system"] in SYSTEMS and ticket["case_id"] in {c["case_id"] for c in b["cases"]}, "JOB_OUTSIDE_PERMIT")
    require(b["runtime"] == runtime(), "RUNTIME_MISMATCH")
    require(b["code_sha256"] == codes(), "CODE_HASH_MISMATCH")
    require(digest(canonical(load("bm25_baseline").SPEC)) == SPEC["bm25_spec_sha256"], "FROZEN_BM25_SPEC_MISMATCH")
    require(read(run_slot(permit["issuer_registry"], b["run_id"], "issued"))["payload_sha256"] == digest(canonical(b)), "ISSUANCE_MISMATCH")
    a = load("experiment_adapter")
    for c in b["cases"]:
        require(describe(c["input"], c["index"], c["repo"], c["package"]) == c, "REQUEST_BINDING_MISMATCH")
        require(file_hash(c["input"]) == c["input_file_sha256"] and file_hash(c["index"]) == c["index_sha256"]
                and file_hash(c["index"] + ".meta.json") == c["metadata_sha256"], "INPUT_INDEX_METADATA_MISMATCH")
        index = read(c["index"]); manifest = a.SE.manifest_for(index)
        require(a.SE.manifest_hash(manifest) == c["source_manifest_sha256"], "MANIFEST_MISMATCH")
        actual = {p.relative_to(Path(c["repo"])).as_posix() for p in (Path(c["repo"])/"src/main/java").rglob("*.java") if p.is_file()}
        require(actual == {m["file_path"] for m in manifest}, "SOURCE_FILE_SET_MISMATCH")
        for m in manifest: require(file_hash(Path(c["repo"])/m["file_path"]) == m["source_sha256"], "STALE_SOURCE")
        if b["scope"] == "SYNTHETIC_ONLY":
            require(not any(m["file_path"].startswith("src/main/java/org/jsoup/") for m in manifest), "REAL_SOURCE_RENAMING")
        request = read(Path(c["package"])/"request.json")
        frozen, _ = a._issue(c["input"], c["case_id"])
        require(frozen["issue_text"] == request["issue_text"] and frozen["repo_id"] == c["repo_id"]
                and frozen["source_snapshot_id"] == c["source_snapshot_id"], "FROZEN_INPUT_REQUEST_MISMATCH")
        if b["scope"] == "SYNTHETIC_ONLY": require(synthetic_request(request), "REAL_INPUT_RENAMING")
        require(request["catalog"] == a.make_catalog(index, c["repo_id"], c["source_snapshot_id"]), "INCOMPLETE_CATALOG")
    outside(b["output"], [p for c in b["cases"] for p in (c["repo"], c["package"])])
    return permit, registry


def job_output(body, cid, system):
    ordinal = [c["case_id"] for c in body["cases"]].index(cid)
    return Path(body["output"]) / "predictions" / (str(ordinal) + ("-rule" if system == "Rule-only" else "-bm25"))


def claim(ticket, request, producer, output, stage):
    require(stage in {"parent", "worker"}, "INVALID_CLAIM_STAGE")
    permit, registry = verify_disk(ticket); b = permit["body"]
    require(Path(output).resolve() == job_output(b, ticket["case_id"], ticket["system"]).resolve(), "OUTPUT_BINDING_MISMATCH")
    require(read(run_slot(permit["issuer_registry"], b["run_id"], "started"))["permit_sha256"] == ticket["permit_sha256"], "CAMPAIGN_NOT_STARTED")
    c = next(c for c in b["cases"] if c["case_id"] == ticket["case_id"])
    require(request == read(Path(c["package"])/"request.json"), "JOB_REQUEST_MISMATCH")
    bridge = load("baseline_producers")
    raw, _ = bridge.capsule_bytes("rule" if ticket["system"] == "Rule-only" else "bm25", controlled=True)
    require(file_hash(producer) == digest(raw), "UNREGISTERED_PRODUCER")
    suffix = stage + "." + str(b["cases"].index(c)) + "." + ticket["system"]
    parent_suffix = "parent." + str(b["cases"].index(c)) + "." + ticket["system"]
    if stage == "worker":
        parent = read(run_slot(permit["issuer_registry"], b["run_id"], parent_suffix))
        require(parent["permit_sha256"] == ticket["permit_sha256"] and parent["producer_sha256"] == digest(raw), "PARENT_CLAIM_MISMATCH")
    proof = dict(permit_sha256=ticket["permit_sha256"], producer_sha256=digest(raw), pid=os.getpid(), stage=stage,
                 case_id=ticket["case_id"], system=ticket["system"], output=str(Path(output).resolve()))
    put(run_slot(permit["issuer_registry"], b["run_id"], suffix), proof)
    if stage == "parent": return {"run_id": b["run_id"], "permit_sha256": ticket["permit_sha256"], "system": ticket["system"]}
    context = dict(permit=permit, key=registry["key"], approval_pin=registry["real_approval_pins"].get(b["run_id"]),
                   proof=proof, proof_mac=sign(registry["key"], proof), verifier_source=Path(__file__).read_text(encoding="utf-8"))
    # Anchor the producer's read-free checks to a disk-validated claim in this
    # process, not a caller-supplied key plus a self-signed dictionary.
    sys._issuelens_c1_active_context = digest(canonical(context))
    validate_context(context, request, ticket["system"])
    return context


def validate_context(context, request, system):
    """Producer-side independent, read-free validation under unchanged B1 guard."""
    require(set(context) == {"permit", "key", "approval_pin", "proof", "proof_mac", "verifier_source"}, "INVALID_CONTEXT_FIELDS")
    permit = context["permit"]; b = permit["body"]; proof = context["proof"]
    validate_shape(permit, context["key"], context["approval_pin"])
    require(set(proof) == {"permit_sha256", "producer_sha256", "pid", "stage", "case_id", "system", "output"}
            and proof["stage"] == "worker" and proof["pid"] == os.getpid() and proof["system"] == system
            and proof["case_id"] == request["case_id"] and context["proof_mac"] == sign(context["key"], proof), "INVALID_WORKER_PROOF")
    require(proof["permit_sha256"] == digest(canonical(permit)), "CONTEXT_PERMIT_MISMATCH")
    require(b["runtime"]["executable"] == str(Path(sys.executable).resolve()) and b["runtime"]["version"] == sys.version, "PRODUCER_RUNTIME_MISMATCH")
    require(digest(context["verifier_source"].encode()) == b["code_sha256"]["controlled_authorization.py"], "VERIFIER_HASH_MISMATCH")
    c = next(c for c in b["cases"] if c["case_id"] == request["case_id"])
    require(all(request[k] == c[k] for k in ("repo_id", "source_snapshot_id", "input_file_sha256", "issue_text_sha256", "index_sha256", "metadata_sha256", "source_manifest_sha256", "catalog_sha256")), "PRODUCER_BINDING_MISMATCH")
    require(digest(canonical(request)) == c["request_canonical_sha256"] and
            request["catalog_sha256"] == digest(canonical(request["catalog"])), "PRODUCER_CATALOG_MISMATCH")
    require(all(request["code_sha256"][name] == b["code_sha256"][name] for name in request["code_sha256"]), "PRODUCER_CODE_BINDING")
    require(len(request["catalog"]) == c["candidate_count"] and request["candidate_k"] == 5, "PRODUCER_COUNT_MISMATCH")
    require(str(Path(proof["output"]).resolve()) == str(job_output(b, c["case_id"], system).resolve())
            and str(Path.cwd().resolve()) == proof["output"], "PRODUCER_OUTPUT_MISMATCH")
    require(getattr(sys, "_issuelens_c1_active_context", None) == digest(canonical(context)), "CONTEXT_NOT_REGISTERED_BY_WORKER")


def execute(permit_path, permit_sha256):
    """Prediction-only campaign: no cohort/GT arguments, eight independent jobs."""
    permit = read(permit_path); b = permit["body"]
    ticket = dict(permit_path=str(Path(permit_path).resolve()), permit_sha256=permit_sha256,
                  run_id=b["run_id"], case_id=b["cases"][0]["case_id"], system=SYSTEMS[0])
    verify_disk(ticket)
    out = outside(b["output"], [p for c in b["cases"] for p in (c["repo"], c["package"])]); require(not out.exists(), "OUTPUT_EXISTS")
    put(run_slot(permit["issuer_registry"], b["run_id"], "started"), {"permit_sha256": permit_sha256})
    out.mkdir(parents=True); put(out / "permit.json", permit)
    a = load("experiment_adapter"); bridge = load("baseline_producers")
    results = []
    for c in b["cases"]:
        for system in SYSTEMS:
            job = {**ticket, "case_id": c["case_id"], "system": system}
            target = job_output(b, c["case_id"], system)
            capsule = out / (str(len(results)) + ".producer.py")
            try:
                raw, _ = bridge.capsule_bytes("rule" if system == SYSTEMS[0] else "bm25", controlled=True)
                with capsule.open("xb") as f: f.write(raw)
                receipt = a.predict(c["package"], capsule, target, authorization=job)
                results.append({**receipt, "case_id": c["case_id"], "system": system, "prediction_dir": str(target)})
            except Exception as exc:
                results.append(dict(case_id=c["case_id"], system=system, prediction_status="EXECUTION_ERROR", error_type=type(exc).__name__, detail=str(exc)))
    put(out / "campaign.json", dict(run_id=b["run_id"], N=4, K=5, results=results, permit_sha256=permit_sha256,
        report_status="COMPLETE", overall_verdict="PASS" if all(r["prediction_status"] in {"SUCCESS", "EMPTY"} for r in results) else "FAIL"))
    files = [{"path": p.relative_to(out).as_posix(), "sha256": file_hash(p)} for p in sorted(out.rglob('*')) if p.is_file()]
    put(out / "seal.json", {"files": files})
    return digest((out / "seal.json").read_bytes())


def evaluate_sealed(output, seal_sha256, cohort_path, out_dir):
    """Independent GT-reading stage, only after externally pinned sealing."""
    out = Path(output); require(file_hash(out / "seal.json") == seal_sha256, "SEAL_HASH_MISMATCH")
    seal = read(out / "seal.json")
    require({r["path"] for r in seal["files"]} == {p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file() and p != out/"seal.json"}, "SEALED_FILE_SET_CHANGED")
    for row in seal["files"]:
        p = (out / row["path"]).resolve(); require(out.resolve() in p.parents, "SEAL_PATH_ESCAPE")
        require(file_hash(p) == row["sha256"], "SEALED_FILE_CHANGED")
    permit = read(out / "permit.json"); body = permit["body"]
    require(Path(body["output"]).resolve() == out.resolve(), "SEALED_OUTPUT_BINDING")
    verify_disk(dict(permit_path=str(out/"permit.json"), permit_sha256=file_hash(out/"permit.json"), run_id=body["run_id"],
                     case_id=body["cases"][0]["case_id"], system=SYSTEMS[0]))
    target = outside(out_dir, [out]); require(not target.exists(), "OUTPUT_EXISTS"); target.mkdir(parents=True)
    base = read(cohort_path); campaign = read(out / "campaign.json"); a = load("experiment_adapter")
    reports = {}
    for system in SYSTEMS:
        job = json.loads(json.dumps(base)); require(len(job["case_ids"]) == 4, "FIXED_FOUR_CASES_REQUIRED")
        for entry in job["cases"]:
            match = next((r for r in campaign["results"] if r["system"] == system and r["case_id"] == entry["case_id"]), {})
            entry.update(prediction_dir=match.get("prediction_dir", str(out / "MISSING")), prediction_sha256=match.get("prediction_sha256", "0"*64))
        path = target / (system + ".cohort.json"); put(path, job)
        reports[system] = a.aggregate(path, target / system)
    return reports


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    q = sub.add_parser("qualify-synthetic")
    for name in ("issuer", "cohort", "out-dir"): q.add_argument("--" + name, required=True)
    q = sub.add_parser("issue-synthetic")
    for name in ("issuer", "run-id", "eligibility", "plan", "output", "permit"): q.add_argument("--" + name, required=True)
    q = sub.add_parser("qualify-real")
    for name in ("issuer", "cohort", "out-dir", "approval"): q.add_argument("--" + name, required=True)
    q = sub.add_parser("issue-real")
    for name in ("issuer", "run-id", "eligibility", "plan", "output", "permit", "approval"): q.add_argument("--" + name, required=True)
    q = sub.add_parser("execute")
    q.add_argument("--permit", required=True); q.add_argument("--permit-sha256", required=True)
    q = sub.add_parser("evaluate")
    for name in ("output", "seal-sha256", "cohort", "out-dir"): q.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        if args.command == "qualify-real":
            qualify_real(args.issuer, args.cohort, args.out_dir, args.approval)
            result = {"status": "QUALIFIED_REAL"}
        elif args.command == "issue-real":
            plan = read(args.plan); require(isinstance(plan, list) and len(plan) == 4, "FIXED_FOUR_CASES_REQUIRED")
            cases = [describe(**e) for e in plan]
            result = {"permit_sha256": issue_real(args.issuer, args.run_id, read(args.eligibility), cases, args.output, args.permit, args.approval)}
        elif args.command == "qualify-synthetic":
            qualify_synthetic(args.issuer, args.cohort, args.out_dir); result = {"status": "QUALIFIED_SYNTHETIC"}
        elif args.command == "issue-synthetic":
            plan = read(args.plan); require(isinstance(plan, list) and len(plan) == 4, "FIXED_FOUR_CASES_REQUIRED")
            cases = [describe(**e) for e in plan]
            result = {"permit_sha256": issue_synthetic(args.issuer, args.run_id, read(args.eligibility), cases, args.output, args.permit)}
        elif args.command == "execute":
            result = {"seal_sha256": execute(args.permit, args.permit_sha256)}
            campaign = read(Path(read(args.permit)["body"]["output"])/"campaign.json")
            result["overall_verdict"] = campaign["overall_verdict"]
            if campaign["overall_verdict"] != "PASS":
                print(json.dumps(result, ensure_ascii=False)); return 2
        else:
            reports = evaluate_sealed(args.output, args.seal_sha256, args.cohort, args.out_dir)
            result = {name: {k: r[k] for k in ("tool_status", "exit_code")} for name, r in reports.items()}
        print(json.dumps(result, ensure_ascii=False)); return 0
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False), file=sys.stderr); return 2


if __name__ == "__main__": sys.exit(main())
