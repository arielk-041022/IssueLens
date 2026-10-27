"""EXP001-B2 BM25_METADATA_V1. Pure GT-free B1 producer; synthetic use only.

No file/network access, Rule scorer, candidate truncation, or external dependency.
Use experiment_adapter.predict to validate/seal request and prediction bytes.
"""
from collections import Counter
import hashlib
import json
import math
import re
import unicodedata


IDENTITY = ("repo_id", "source_snapshot_id", "file_path", "method_id")
FIELDS = ("name", "class", "signature", "file", "annotations", "strings", "calls")
K1 = 1.2
B = 0.75
AUTHORIZATION_CODE_SHA256 = "d2b4cbd27fa72759434faf8c3705fe1c38cf544917f2f7919ddb3199fca80cab"
REAL_SCOPE_SNAPSHOTS = ['sha256:8bc6a817c5be85301cb443bb13c1f399821417f4188b2a332f4ee1dc76ad49d5', 'sha256:9b4e5d854e6c9b6824972fc0dbdef11af2e7bf541805d51b9267ce7eb6f72300', 'sha256:dfa01854c9992baa5dd7aa7dc7eefc1f4cc49eb03389569113b6a5327ea7c74b', 'sha256:eca1ac73179f98c878c8f655be75ce153096456a852f97d006ee03e0b677c4f3']
REAL_SCOPE_QUERY_HASHES = ['0b8342d3c4ac9046595ff47621b3cf16735371952ae5eeadff900a1bcc34799d', '57f7170c9a4c898a11d64fec1d7a1a79fe67acd2483ab98cdf802eb56317aedb', '63df915e19cfb60221068f36baabf85da529ee76dd602de1169e8444e84347d7', 'db59626a6b115b567e2c5b9d3ba50b160a91e4101a43a02b53edd5168feff00b']
SPEC = {
    "protocol_id": "EXP001-B2-BM25_METADATA_V1",
    "fields": list(FIELDS),
    "field_join": "LF; array elements joined by LF; preserve order, duplicates and empty strings",
    "field_weights": "NONE; every token occurrence has unit TF weight",
    "tokenizer": "ASCII camel boundaries (?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z]); Unicode [^\\W_]+ runs; str.lower",
    "digits": "retained within runs; no letter/digit split except digit-to-ASCII-uppercase camel boundary",
    "normalization": "NONE; no NFC, casefold, stopwords, stemming, expansion, whole-identifier augmentation or truncation",
    "query_terms": "unique terms sorted by Python Unicode code-point order; no query TF weight",
    "tf": "token occurrence count in concatenated method document",
    "df": "number of all catalog documents containing the term",
    "idf": "ln(1 + (N - df + 0.5)/(df + 0.5)); natural log via log1p",
    "score": "fsum(idf(t)*tf(t,d)*(k1+1)/(tf(t,d)+k1*(1-b+b*dl/avgdl))) over unique query terms",
    "k1": K1,
    "b": B,
    "length": "dl includes all document tokens; avgdl=sum(dl)/N including empty documents",
    "empty": "empty query/document or avgdl=0 gives zero scores; N=0 gives EMPTY",
    "order": "descending unrounded finite binary64 score, then original catalog ordinal ascending",
    "candidate_scope": "ALL_METHODS_IN_VALIDATED_COMPLETE_B1_CATALOG; includes bodyless methods",
    "real_case_execution": "DISABLED; fixture-* only; no tuning on the four real cases",
}
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_WORDS = re.compile(r"[^\W_]+", flags=re.UNICODE)


class BM25Error(ValueError):
    """Invalid request/result. Never converted into an empty successful run."""


def require(ok, reason):
    if not ok:
        raise BM25Error(reason)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def is_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def text(value):
    return isinstance(value, str) and bool(value.strip())


def tokenize(value):
    require(isinstance(value, str), "NON_STRING_TEXT")
    # Boundaries are computed before lowercasing. Underscore is a separator.
    return [word.lower() for word in _WORDS.findall(_CAMEL.sub(" ", value))]


def document(features):
    require(isinstance(features, dict) and set(features) == set(FIELDS), "INVALID_FEATURE_FIELDS")
    parts = []
    for field in FIELDS:
        value = features[field]
        if field in FIELDS[:4]:
            require(isinstance(value, str), "NON_STRING_FEATURE")
            parts.append(value)
        else:
            require(isinstance(value, list) and all(isinstance(s, str) for s in value), "INVALID_FEATURE_ARRAY")
            parts.append("\n".join(value))
    return "\n".join(parts)


def validate_request(request, *, authorization=None):
    """Defensive B1 schema checks, independent of Rule or any GT.

    The B1 controller additionally verifies the package/source and current code
    hashes. A self-consistent request alone is not proof of full-source coverage.
    """
    expected = {"schema_version", "case_id", "repo_id", "source_snapshot_id", "issue_text",
                "input_file_sha256", "issue_text_sha256", "index_sha256", "metadata_sha256",
                "source_manifest_sha256", "candidate_k", "catalog", "catalog_sha256", "code_sha256"}
    require(isinstance(request, dict) and set(request) == expected, "UNKNOWN_OR_MISSING_REQUEST_FIELDS")
    require(request["schema_version"] == "prediction-request-v1", "UNSUPPORTED_REQUEST_SCHEMA")
    if authorization is None:
        require(text(request["case_id"]) and request["case_id"].startswith("fixture-"), "B2_SYNTHETIC_ONLY")
        require(request["repo_id"] != "jhy/jsoup" and request["source_snapshot_id"] not in REAL_SCOPE_SNAPSHOTS
                and request["issue_text_sha256"] not in REAL_SCOPE_QUERY_HASHES
                and not any(r.get("file_path", "").startswith("src/main/java/org/jsoup/") for r in request["catalog"]), "REAL_INPUT_RENAMING")
    else:
        source = authorization.get("verifier_source") if isinstance(authorization, dict) else None
        require(isinstance(source, str) and sha(source.encode()) == AUTHORIZATION_CODE_SHA256, "UNTRUSTED_AUTHORIZATION_CODE")
        verifier = {"__file__": "controlled_authorization.py", "__name__": "bm25_authority"}
        exec(compile(source, verifier["__file__"], "exec"), verifier)
        verifier["validate_context"](authorization, request, "BM25-only")
    require(type(request["candidate_k"]) is int and request["candidate_k"] == 5, "INVALID_CANDIDATE_K")
    require(text(request["repo_id"]) and isinstance(request["issue_text"], str), "INVALID_REQUEST_TEXT")
    for field in ("input_file_sha256", "issue_text_sha256", "index_sha256", "metadata_sha256",
                  "source_manifest_sha256", "catalog_sha256"):
        require(is_hash(request[field]), "INVALID_REQUEST_HASH")
    require(request["source_snapshot_id"] == "sha256:" + request["source_manifest_sha256"], "SOURCE_SNAPSHOT_MISMATCH")
    require(sha(request["issue_text"].encode("utf-8")) == request["issue_text_sha256"], "ISSUE_HASH_MISMATCH")
    code = request["code_sha256"]
    require(isinstance(code, dict) and set(code) == {"experiment_adapter.py", "strict_evaluation.py",
            "evidence_validator.py", "retriever.py"} and all(is_hash(h) for h in code.values()), "INVALID_CODE_BINDING")
    catalog = request["catalog"]
    require(isinstance(catalog, list) and sha(canonical(catalog)) == request["catalog_sha256"], "CATALOG_HASH_MISMATCH")
    ids = set()
    for ordinal, record in enumerate(catalog):
        require(isinstance(record, dict) and set(record) == set(IDENTITY) | {"ordinal", "has_body", "features"}, "INVALID_CATALOG_FIELDS")
        require(all(text(record[k]) for k in IDENTITY), "MISSING_METHOD_IDENTITY")
        require(record["repo_id"] == request["repo_id"] and record["source_snapshot_id"] == request["source_snapshot_id"], "CROSS_SNAPSHOT_IDENTITY")
        require(record["method_id"].startswith("java-source-v1:"), "INVALID_METHOD_ID_SCHEME")
        path = record["file_path"]
        pieces = path.split("/")
        require("\\" not in path and ":" not in path and pieces[:3] == ["src", "main", "java"]
                and len(pieces) >= 4 and path.endswith(".java")
                and all(p and p not in {".", ".."} and not p.endswith((".", " ")) for p in pieces)
                and all(ord(c) >= 32 for c in path), "INVALID_FILE_PATH")
        require(record["method_id"] not in ids, "DUPLICATE_METHOD_ID")
        ids.add(record["method_id"])
        require(type(record["ordinal"]) is int and record["ordinal"] == ordinal, "INVALID_ORDINAL")
        require(type(record["has_body"]) is bool, "INVALID_HAS_BODY")
        document(record["features"])
        require(record["features"]["file"] == path, "FEATURE_FILE_MISMATCH")


def score_tokens(documents, query):
    """Fixed BM25 formula; no configurable parameters or candidate selector."""
    require(isinstance(documents, list) and all(isinstance(d, list) and
            all(isinstance(t, str) and t for t in d) for d in documents), "INVALID_DOCUMENT_TOKENS")
    require(isinstance(query, list) and all(isinstance(t, str) and t for t in query), "INVALID_QUERY_TOKENS")
    n = len(documents)
    counts = [Counter(d) for d in documents]
    lengths = [len(d) for d in documents]
    avgdl = sum(lengths) / n if n else 0.0
    terms = sorted(set(query))
    dfs = {t: sum(t in count for count in counts) for t in terms}
    idfs = {t: math.log1p((n - dfs[t] + 0.5) / (dfs[t] + 0.5)) for t in terms}
    scores = []
    for count, length in zip(counts, lengths):
        contributions = []
        if avgdl:
            norm = K1 * (1 - B + B * length / avgdl)
            for term in terms:
                tf = count[term]
                if tf:
                    contributions.append(idfs[term] * tf * (K1 + 1) / (tf + norm))
        score = math.fsum(contributions)
        require(math.isfinite(score) and score >= 0, "NONFINITE_OR_NEGATIVE_SCORE")
        scores.append(score)
    return scores, dict(n=n, lengths=lengths, avgdl=avgdl, query_terms=terms, df=dfs, idf=idfs)


def predict(request, *, authorization=None):
    """B1 producer API. Every catalog method is scored and returned, even at 0."""
    validate_request(request, authorization=authorization)
    catalog = request["catalog"]
    docs = [tokenize(document(record["features"])) for record in catalog]
    query = tokenize(request["issue_text"])
    scores, stats = score_tokens(docs, query)
    order = sorted(range(len(catalog)), key=lambda i: (-scores[i], catalog[i]["ordinal"]))
    ranking = [{**{key: catalog[i][key] for key in IDENTITY}, "score": scores[i]} for i in order]
    telemetry = {
        "system": "BM25-only", "protocol": SPEC, "protocol_sha256": sha(canonical(SPEC)),
        "unicode_database_version": unicodedata.unidata_version,
        "candidate_count": len(catalog), "scored_count": len(scores), "output_count": len(ranking),
        "scope": "FULL_CATALOG", "query_tokens_sha256": sha(canonical(query)),
        "documents_tokens_sha256": sha(canonical(docs)), "statistics": stats,
        "usage": {"status": "NOT_APPLICABLE_OFFLINE"},
        "attempts": [], "requests": [], "exceptions": [],
    }
    return {"status": "SUCCESS" if catalog else "EMPTY", "ranking": ranking, "telemetry": telemetry}
