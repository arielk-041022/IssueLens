"""Offline TASK 001 evidence validation; standard library only, no independent AST parse."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

SCHEMA = "2.0"
POSITION = "1-based-inclusive-UTF16-tab1"
PARSE_STATES = ("PARSED", "PARTIAL", "FAILED", "READ_ERROR")
EVIDENCE_TYPES = {"ANNOTATION", "STRING_LITERAL", "METHOD_CALL"}
IDENTITY_STATES = {"AVAILABLE", "CONFLICT", "UNAVAILABLE_PARSE", "UNSUPPORTED_LOCAL_TYPE"}
LOCATION_STATES = {"AVAILABLE", "UNAVAILABLE_MAPPING", "UNAVAILABLE_PARSE"}
COORDS = ("start_line", "start_column", "end_line", "end_column")
SHA = re.compile(r"[0-9a-f]{64}\Z")
UNICODE_ESCAPE = re.compile(r"\\u+[0-9a-fA-F]{4}")
V2_KEYS = {"method_id", "evidence_occurrences", "source_sha256", "identity_status", "location_status"}
TERMINAL_PENDING = "NOT_CHECKED"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def unicode_literal_mapping_safe(source):
    """Conservative raw lexical gate, not an independent Java AST parser.

    Only exact single-escape character literals for NUL/replacement character
    are supported. Even ineligible, malformed or comment-contained markers are
    rejected. No Unicode translation or recursive escape interpretation occurs.
    """
    markers = {i for i in range(len(source) - 1) if source[i:i + 2] == "\\u"}
    if not markers:
        return True
    allowed = set()
    i = 0
    while i < len(source):
        if source.startswith("//", i):
            i += 2
            while i < len(source) and source[i] not in "\r\n":
                i += 1
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            if end < 0:
                return False
            i = end + 2
        elif source.startswith('"""', i):
            return False  # Text-block lexical interactions are outside this gate.
        elif source[i] in "\"'":
            quote = source[i]
            raw = source[i:i + 8]
            if quote == "'" and raw in ("'\\u0000'", "'\\uFFFD'"):
                # The preceding raw quote starts a character token in CODE state;
                # there is exactly one backslash, one u, four hex digits, one close.
                allowed.add(i + 1)
                i += 8
                continue
            i += 1
            closed = False
            while i < len(source) and source[i] not in "\r\n":
                if source[i] == "\\":
                    i += 2
                elif source[i] == quote:
                    i += 1
                    closed = True
                    break
                else:
                    i += 1
            if not closed:
                return False
        else:
            i += 1
    return markers == allowed


def jackson_string(value):
    """Jackson 2.18.2 UTF8JsonGenerator, default escaping; not Python dumps defaults.

    UTF-8 generator emits BMP non-ASCII literally and supplementary code points as
    uppercase surrogate escapes. TASK 001 uses DefaultPrettyPrinter array spaces.
    """
    if not isinstance(value, str):
        raise ValueError("Expected string")
    value.encode("utf-8", errors="strict")  # reject unpaired surrogates
    short = {'"': '\\"', '\\': '\\\\', '\b': '\\b', '\f': '\\f',
             '\n': '\\n', '\r': '\\r', '\t': '\\t'}
    out = ['"']
    for char in value:
        code = ord(char)
        if char in short:
            out.append(short[char])
        elif code < 32:
            out.append("\\u%04X" % code)
        elif code > 0xFFFF:
            number = code - 0x10000
            out.append("\\u%04X\\u%04X" % (0xD800 + (number >> 10), 0xDC00 + (number & 1023)))
        else:
            out.append(char)
    return "".join(out) + '"'


def evidence_id_bytes(file_path, source_hash, method_id, evidence_type, coordinates):
    values = [file_path, source_hash, method_id, evidence_type, *coordinates]
    if len(coordinates) != 4 or any(type(v) is not int for v in coordinates):
        raise ValueError("Expected four integer coordinates")
    serialized = "[ " + ", ".join(jackson_string(v) if isinstance(v, str) else str(v)
                                  for v in values) + " ]"
    return serialized.encode("utf-8")


def compute_evidence_id(file_path, source_hash, method_id, evidence_type, coordinates):
    return "sha256:" + sha256(evidence_id_bytes(file_path, source_hash, method_id,
                                               evidence_type, coordinates))


class ValidationFault(Exception):
    def __init__(self, stage, status, reason_code, detail, pointer=""):
        super().__init__(detail)
        self.issue = dict(stage=stage, status=status, reason_code=reason_code,
                          detail=str(detail), record_pointer=pointer)


def fault(stage, status, reason, detail, pointer=""):
    raise ValidationFault(stage, status, reason, detail, pointer)


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            key.encode("utf-8", errors="strict")
            if key in result:
                raise ValueError("Duplicate JSON key: " + key)
            result[key] = value
        return result

    def constant(value):
        raise ValueError("Non-standard JSON constant: " + value)

    parsed = json.loads(data.decode("utf-8", errors="strict"), object_pairs_hook=pairs,
                        parse_constant=constant)

    def check_strings(obj):
        if isinstance(obj, str):
            obj.encode("utf-8", errors="strict")
        elif isinstance(obj, dict):
            for value in obj.values():
                check_strings(value)
        elif isinstance(obj, list):
            for value in obj:
                check_strings(value)

    check_strings(parsed)
    return parsed


def _text(value):
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8", errors="strict")
        return True
    except UnicodeError:
        return False


def _integer(value):
    return type(value) is int and 0 <= value <= 2147483647


def _has_v2(obj):
    if isinstance(obj, dict):
        return bool(V2_KEYS.intersection(obj)) or any(_has_v2(v) for v in obj.values())
    return isinstance(obj, list) and any(_has_v2(v) for v in obj)


def safe_source_path(root, relative, pointer=""):
    """Reject noncanonical Windows/Posix aliases and all descendant reparse points."""
    if not _text(relative) or not relative:
        fault("PATH", "INVALID_PATH", "INVALID_RELATIVE_PATH", "Path must be a nonempty string", pointer)
    parts = relative.split("/")
    reserved = re.compile(r"(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|CLOCK\$|COM[1-9¹²³]|LPT[1-9¹²³])\Z", re.I)
    if (relative.startswith("/") or "\\" in relative or ":" in relative
            or any(ord(c) < 32 or c in '<>"|?*' for c in relative)
            or any(not p or p in {".", ".."} or p.endswith((".", " "))
                   or reserved.fullmatch(p.split(".")[0].rstrip(" "))
                   for p in parts)
            or parts[:3] != ["src", "main", "java"] or len(parts) < 4 or not relative.endswith(".java")):
        fault("PATH", "INVALID_PATH", "NONCANONICAL_SOURCE_PATH", relative, pointer)
    candidate = root
    for part in parts:
        candidate = candidate / part
        try:
            attrs = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            fault("PATH", "INVALID_PATH", "PATH_INSPECTION_FAILED", str(exc), pointer)
        if stat.S_ISLNK(attrs.st_mode) or getattr(attrs, "st_file_attributes", 0) & 0x400:
            fault("PATH", "INVALID_PATH", "REPARSE_POINT_REJECTED", relative, pointer)
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root)
    except (ValueError, OSError, RuntimeError) as exc:
        fault("PATH", "INVALID_PATH", "SOURCE_OUTSIDE_REPOSITORY", str(exc), pointer)
    return candidate


class SourceLines:
    """Only CR/LF split lines; convert UTF-16 columns using legal code point boundaries."""
    def __init__(self, text):
        self.text = text
        self.lines = re.split(r"\r\n|\r|\n", text)
        self.starts = [0]
        for match in re.finditer(r"\r\n|\r|\n", text):
            self.starts.append(match.end())
        self.boundaries = {}

    def _boundaries(self, line):
        if line not in self.boundaries:
            unit = 0
            offsets = {0: 0}
            for index, char in enumerate(self.lines[line - 1]):
                unit += 2 if ord(char) > 0xFFFF else 1
                offsets[unit] = index + 1
            self.boundaries[line] = offsets
        return self.boundaries[line]

    def extract(self, record):
        coords = [record.get(k) for k in COORDS]
        if any(type(v) is not int or not 1 <= v <= 2147483647 for v in coords):
            raise ValueError("Coordinates must be positive Java integers, not booleans")
        sl, sc, el, ec = coords
        if sl > el or el > len(self.lines) or (sl == el and sc > ec):
            raise ValueError("Reversed or nonexistent line/column range")
        begin_map, end_map = self._boundaries(sl), self._boundaries(el)
        if sc - 1 not in begin_map or ec not in end_map:
            raise ValueError("Range outside line content or splits a UTF-16 surrogate pair")
        begin = self.starts[sl - 1] + begin_map[sc - 1]
        end = self.starts[el - 1] + end_map[ec]
        # A begin at EOL, empty end line or newline-only endpoint is not a node location.
        if begin_map[sc - 1] >= len(self.lines[sl - 1]) or end_map[ec] == 0 or begin >= end:
            raise ValueError("Endpoint is not a source character")
        return self.text[begin:end]


def _new_report(repo, index, meta):
    return {
        "report_schema_version": "1.0", "validator_version": "1.0",
        "verification_scope": {
            "checks": ["INDEX_BINDING", "PATH", "SOURCE_BYTES", "DECLARED_OWNERSHIP",
                       "UTF16_RANGE", "EXACT_SNIPPET", "EVIDENCE_ID"],
            "independent_ast_parse": False,
            "evidence_type_origin": "TASK001_JAVAPARSER_INDEX_DECLARATION",
            "method_identity_scope": "DECLARED_SIGNATURE_CONSISTENCY_NOT_SEMANTIC_RESOLUTION",
        },
        "report_status": "COMPLETE", "overall_verdict": "FAIL", "exit_code": 1,
        "inputs": {"repo_root": str(repo), "index_path": str(index), "metadata_path": str(meta)},
        "index_integrity": {"status": "NOT_CHECKED"},
        "file_results": [], "method_results": [], "evidence_results": [], "issues": [],
    }


def _issue(report, stage, status, reason, detail, pointer=""):
    report["issues"].append(dict(stage=stage, status=status, reason_code=reason,
                                  detail=str(detail), record_pointer=pointer))


def _record_failure(report, row, stage, status, reason, detail):
    row.update(status=status, reason_code=reason)
    _issue(report, stage, status, reason, detail, row["record_pointer"])


def _finish(report):
    if not report["evidence_results"] and not report["issues"]:
        _issue(report, "EVIDENCE", "UNVERIFIABLE", "NO_EVIDENCE", "No evidence to validate")
    fail = bool(report["issues"]) or report["report_status"] == "ABORTED"
    for row in report["evidence_results"]:
        if row["status"] == "CANDIDATE_PASS":
            row.update(status="UNVERIFIABLE" if fail else "VERIFIED",
                       reason_code="BATCH_FAIL_CLOSED" if fail else "ALL_SOURCE_CHECKS_PASSED")
        elif row["status"] == TERMINAL_PENDING:
            row.update(status="UNVERIFIABLE", reason_code="PRECONDITION_FAILED")
    counts = Counter(row["status"] for row in report["evidence_results"])
    report["summary"] = {
        "files_total": len(report["file_results"]), "methods_total": len(report["method_results"]),
        "evidence_total": len(report["evidence_results"]),
        "verified": counts.get("VERIFIED", 0), "unverifiable": counts.get("UNVERIFIABLE", 0),
        "failed": sum(n for key, n in counts.items() if key not in {"VERIFIED", "UNVERIFIABLE"}),
        "evidence_status_counts": dict(sorted(counts.items())), "blocking_issues": len(report["issues"]),
    }
    report["overall_verdict"] = "FAIL" if fail else "PASS"
    hard = any(i["status"] in {"INDEX_INTEGRITY_ERROR", "INVALID_PATH"} for i in report["issues"])
    report["exit_code"] = 2 if hard or report["report_status"] == "ABORTED" else 1 if fail else 0
    return report


def _check_schema(index):
    def require(condition, detail, pointer):
        if not condition:
            fault("SCHEMA", "INDEX_INTEGRITY_ERROR", "INVALID_INDEX_SCHEMA", detail, pointer)
    require(isinstance(index, list), "Main index must be an array", "")
    for fi, f in enumerate(index):
        fp = f"/{fi}"
        require(isinstance(f, dict), "File must be an object", fp)
        require(isinstance(f.get("classes"), list) and _text(f.get("file")) and _text(f.get("package")),
                "Invalid file fields", fp)
        require(f.get("parse_status") in PARSE_STATES, "Invalid parse_status", fp)
        for key in ("parse_problems", "limitations"):
            require(isinstance(f.get(key), list) and all(_text(x) for x in f[key]), "Invalid " + key, fp)
        require(f.get("source_sha256") is None or (_text(f["source_sha256"]) and SHA.fullmatch(f["source_sha256"])),
                "Invalid source_sha256", fp)
        for ci, c in enumerate(f["classes"]):
            cp = fp + f"/classes/{ci}"
            require(isinstance(c, dict) and _text(c.get("name")) and isinstance(c.get("methods"), list),
                    "Invalid class", cp)
            for mi, m in enumerate(c["methods"]):
                mp = cp + f"/methods/{mi}"
                require(isinstance(m, dict), "Method must be an object", mp)
                for key in ("name", "signature", "qualified_class_name"):
                    require(_text(m.get(key)), "Invalid " + key, mp)
                require(m.get("method_id") is None or _text(m["method_id"]), "Invalid method_id", mp)
                require(type(m.get("line")) is int and type(m.get("has_body")) is bool,
                        "Invalid legacy line or has_body", mp)
                require(_text(m.get("identity_status")) and m["identity_status"] in IDENTITY_STATES
                        and _text(m.get("location_status")) and m["location_status"] in LOCATION_STATES,
                        "Invalid identity/location status", mp)
                require(_integer(m.get("evidence_omissions")), "Invalid evidence_omissions", mp)
                for key in ("annotations", "strings", "calls"):
                    require(isinstance(m.get(key), list) and all(_text(x) for x in m[key]), "Invalid " + key, mp)
                require(isinstance(m.get("evidence_occurrences"), list), "Missing occurrence array", mp)
                for ei, e in enumerate(m["evidence_occurrences"]):
                    ep = mp + f"/evidence_occurrences/{ei}"
                    require(isinstance(e, dict), "Evidence must be an object", ep)
                    for key in ("evidence_id", "evidence_type", "file_path", "source_snippet"):
                        require(_text(e.get(key)), "Invalid " + key, ep)


def _catalog(report, index):
    files, methods, evidences = [], [], []
    for fi, f in enumerate(index):
        fp = f"/{fi}"
        fr = dict(record_pointer=fp, file_path=f["file"], status="NOT_CHECKED")
        report["file_results"].append(fr)
        files.append((f, fr))
        for ci, c in enumerate(f["classes"]):
            for mi, m in enumerate(c["methods"]):
                mp = fp + f"/classes/{ci}/methods/{mi}"
                mr = dict(record_pointer=mp, method_id=m["method_id"], status="NOT_CHECKED")
                report["method_results"].append(mr)
                methods.append((f, c, m, mr))
                for ei, e in enumerate(m["evidence_occurrences"]):
                    er = dict(record_pointer=mp + f"/evidence_occurrences/{ei}", evidence_id=e["evidence_id"],
                              method_id=m["method_id"], evidence_type=e["evidence_type"], file_path=e["file_path"],
                              status=TERMINAL_PENDING, reason_code="NOT_CHECKED",
                              range={k: e.get(k) for k in COORDS}, basis={})
                    report["evidence_results"].append(er)
                    evidences.append((f, m, e, er))
    return files, methods, evidences


def _metadata(report, index, meta, methods, evidences, index_path, raw):
    if not isinstance(meta, dict):
        fault("INTEGRITY", "INDEX_INTEGRITY_ERROR", "INVALID_METADATA", "Metadata must be an object")
    expected_hash = meta.get("index_sha256")
    actual_hash = sha256(raw)
    report["index_integrity"] = dict(status="FAIL", expected_sha256=expected_hash, actual_sha256=actual_hash)
    if not _text(expected_hash) or not SHA.fullmatch(expected_hash) or expected_hash != actual_hash:
        fault("INTEGRITY", "INDEX_INTEGRITY_ERROR", "INDEX_HASH_MISMATCH", "Main bytes do not match metadata")
    if meta.get("index_file") != index_path.name:
        fault("INTEGRITY", "INDEX_INTEGRITY_ERROR", "INDEX_FILENAME_MISMATCH", "index_file differs from input basename")
    report["inputs"].update(index_schema_version=meta.get("schema_version"),
                             position_convention=meta.get("position_convention"))
    if (meta.get("schema_version") != SCHEMA or meta.get("position_convention") != POSITION
            or meta.get("method_id_scheme") != "java-source-v1" or meta.get("parser_version") != "3.26.4"
            or meta.get("language_level") != "BLEEDING_EDGE" or meta.get("source_encoding") != "UTF-8"
            or meta.get("preprocess_unicode_escapes") is not False):
        fault("CONFIGURATION", "UNVERIFIABLE", "UNSUPPORTED_INDEX_CONFIGURATION", "Unsupported schema/parser/position settings")
    if meta.get("source_roots") != ["src/main/java"]:
        fault("CONFIGURATION", "INVALID_PATH", "UNSUPPORTED_SOURCE_ROOTS", "Only src/main/java is supported")
    parse_counts = {k: sum(f["parse_status"] == k for f in index) for k in PARSE_STATES}
    counts = {
        "files_total": len(index), "methods_total": len(methods), "evidence_occurrences": len(evidences),
        "identity_unavailable_methods": sum(m["identity_status"] != "AVAILABLE" for _, _, m, _ in methods),
        "location_unavailable_methods": sum(m["location_status"] != "AVAILABLE" for _, _, m, _ in methods),
        "identity_conflict_methods": sum(m["identity_status"] == "CONFLICT" for _, _, m, _ in methods),
    }
    conflict_ids = sorted({"java-source-v1:" + m["qualified_class_name"] + "#" + m["signature"]
                           for _, _, m, _ in methods if m["identity_status"] == "CONFLICT"})
    counts["identity_conflict_groups"] = len(conflict_ids)
    valid_parse_counts = (isinstance(meta.get("parse_counts"), dict)
                          and set(meta["parse_counts"]) == set(PARSE_STATES)
                          and all(_integer(v) for v in meta["parse_counts"].values())
                          and meta["parse_counts"] == parse_counts)
    if (not valid_parse_counts or any(not _integer(meta.get(k)) or meta[k] != n for k, n in counts.items())
            or meta.get("conflicting_method_ids") != conflict_ids):
        fault("INTEGRITY", "INDEX_INTEGRITY_ERROR", "METADATA_COUNTS_MISMATCH", "Metadata counts/conflicts differ from main index")
    report["index_integrity"]["status"] = "PASS"


def _read_source(root, f, pointer):
    path = safe_source_path(root, f["file"], pointer)
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise OSError("Not a regular source file")
            data = handle.read()
            after = os.fstat(handle.fileno())
        safe_source_path(root, f["file"], pointer)
        current = path.stat()
        signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        if signature(before) != signature(after) or signature(after) != signature(current):
            fault("SOURCE", "UNVERIFIABLE", "SOURCE_CHANGED_DURING_READ", f["file"], pointer)
    except FileNotFoundError:
        fault("SOURCE", "MISSING_SOURCE", "SOURCE_NOT_FOUND", f["file"], pointer)
    except OSError as exc:
        fault("SOURCE", "UNVERIFIABLE", "SOURCE_READ_ERROR", str(exc), pointer)
    return data


def _run(report, repo, index_path, meta_path, *, local_protocol=False):
    try:
        root = repo.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Repository root is not a directory")
    except (OSError, ValueError, RuntimeError) as exc:
        fault("REPOSITORY", "INVALID_PATH", "INVALID_REPOSITORY_ROOT", str(exc))
    report["inputs"]["repo_root"] = str(root)
    try:
        raw = index_path.read_bytes()
        report["inputs"]["index_sha256"] = sha256(raw)
        index = strict_json(raw)
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        fault("INDEX_READ", "INDEX_INTEGRITY_ERROR", "INDEX_READ_OR_JSON_ERROR", str(exc))
    try:
        meta_exists = meta_path.exists()
    except OSError as exc:
        fault("METADATA_READ", "INDEX_INTEGRITY_ERROR", "METADATA_READ_OR_JSON_ERROR", str(exc))
    if not meta_exists and not _has_v2(index):
        if not isinstance(index, list):
            fault("SCHEMA", "INDEX_INTEGRITY_ERROR", "INVALID_LEGACY_SCHEMA", "Legacy root must be an array")
        report["index_integrity"]["status"] = "UNVERIFIABLE"
        fault("SCHEMA", "UNVERIFIABLE", "LEGACY_INDEX_NO_EVIDENCE", "Legacy index lacks v2 binding and occurrences")
    _check_schema(index)
    files, methods, evidences = _catalog(report, index)
    try:
        meta_raw = meta_path.read_bytes()
        report["inputs"]["metadata_sha256"] = sha256(meta_raw)
        meta = strict_json(meta_raw)
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        fault("METADATA_READ", "INDEX_INTEGRITY_ERROR", "METADATA_READ_OR_JSON_ERROR", str(exc))
    _metadata(report, index, meta, methods, evidences, index_path, raw)
    # Preflight every path before opening any source, including valid but misbound evidence paths.
    paths = set()
    for f, fr in files:
        candidate = safe_source_path(root, f["file"], fr["record_pointer"])
        key = os.path.normcase(str(candidate))
        if key in paths:
            fault("PATH", "INVALID_PATH", "DUPLICATE_FILE_PATH", f["file"], fr["record_pointer"])
        paths.add(key)
    for f, _, e, er in evidences:
        safe_source_path(root, e["file_path"], er["record_pointer"])
        if e["file_path"] != f["file"]:
            er.update(status="INVALID_PATH", reason_code="EVIDENCE_FILE_OWNERSHIP_MISMATCH")
            fault("PATH", "INVALID_PATH", "EVIDENCE_FILE_OWNERSHIP_MISMATCH", e["file_path"], er["record_pointer"])
    method_counts = Counter(m["method_id"] for _, _, m, _ in methods if m["method_id"] is not None)
    evidence_counts = Counter(e["evidence_id"] for _, _, e, _ in evidences)
    for _, _, e, er in evidences:
        if evidence_counts[e["evidence_id"]] > 1:
            _record_failure(report, er, "OWNERSHIP", "DUPLICATE_EVIDENCE_ID", "DUPLICATE_EVIDENCE_ID",
                            "All occurrences of this evidence_id are rejected")
    snapshots = {}
    for f, fr in files:
        pointer = fr["record_pointer"]
        try:
            data = _read_source(root, f, pointer)
            expected, actual = f["source_sha256"], sha256(data)
            fr.update(expected_source_sha256=expected, actual_source_sha256=actual)
            if expected is None:
                fault("SOURCE", "UNVERIFIABLE", "MISSING_SOURCE_HASH", f["file"], pointer)
            if actual != expected:
                fault("SOURCE", "STALE_SOURCE", "SOURCE_HASH_MISMATCH", f["file"], pointer)
            try:
                text = data.decode("utf-8", errors="strict")
            except UnicodeError as exc:
                fault("SOURCE", "UNVERIFIABLE", "INVALID_UTF8_SOURCE", str(exc), pointer)
            raw_gate = not text.startswith("\ufeff") and unicode_literal_mapping_safe(text)
            opaque_only = False
            if local_protocol and not raw_gate:
                # Reuse the versioned source-binding lexical gate, never AVAILABLE
                # alone. Escapes in a selected method still fail below. No Unicode
                # translation, offset adjustment or file-level evidence upgrade.
                from importlib import import_module
                try:
                    module = import_module((__package__ + "." if __package__ else "") + "metadata_experiment_v2")
                except ModuleNotFoundError as exc:
                    if exc.name != "metadata_experiment_v2":
                        raise
                    # File-based test loaders have the project root, while normal
                    # `python src/...py` entry points have only src on sys.path.
                    module = import_module("src.metadata_experiment_v2")
                opaque_only = module.opaque_source_safe(text)
            if not raw_gate and not opaque_only:
                fault("POSITION", "UNVERIFIABLE", "UNSUPPORTED_RAW_MAPPING", "BOM or unsupported raw Unicode context", pointer)
            if f["parse_status"] != "PARSED" or f["parse_problems"]:
                fault("PARSE", "UNVERIFIABLE", "PARSE_NOT_AVAILABLE", f["parse_status"], pointer)
            blockers = {"SOURCE_TOKEN_MAPPING_UNAVAILABLE", "BOM_RAW_MAPPING_UNSUPPORTED",
                        "UNICODE_ESCAPE_RAW_MAPPING_UNSUPPORTED"}.intersection(f["limitations"])
            if blockers and not (opaque_only and blockers == {"UNICODE_ESCAPE_RAW_MAPPING_UNSUPPORTED"}):
                fault("POSITION", "UNVERIFIABLE", "INDEX_MAPPING_UNAVAILABLE", f["file"], pointer)
            snapshots[f["file"]] = SourceLines(text)
            if opaque_only:
                fr.update(status="UNVERIFIABLE", reason_code="UNSUPPORTED_RAW_MAPPING",
                          local_unescaped_methods_checkable=True)
                _issue(report, "POSITION", "UNVERIFIABLE", "UNSUPPORTED_RAW_MAPPING",
                       "File remains blocked; only individually checked unescaped methods eligible", pointer)
            else:
                fr["status"] = "SOURCE_MATCH"
        except ValidationFault as exc:
            fr.update(status=exc.issue["status"], reason_code=exc.issue["reason_code"])
            report["issues"].append(exc.issue)
    file_rows = {f["file"]: fr for f, fr in files}
    method_rows = {}
    for f, c, m, mr in methods:
        method_rows[id(m)] = mr
        fr = file_rows[f["file"]]
        if fr["status"] != "SOURCE_MATCH" and not fr.get("local_unescaped_methods_checkable", False):
            mr.update(status=fr["status"], reason_code=fr.get("reason_code", "SOURCE_UNAVAILABLE"))
            continue
        mid = m["method_id"]
        expected_id = "java-source-v1:" + m["qualified_class_name"] + "#" + m["signature"]
        name_ok = (m["signature"].startswith(m["name"] + "(") and m["signature"].endswith(")")
                   and m["qualified_class_name"].split(".")[-1] == c["name"]
                   and (not f["package"] or m["qualified_class_name"].startswith(f["package"] + ".")))
        if mid is not None and method_counts[mid] > 1:
            _record_failure(report, mr, "OWNERSHIP", "UNVERIFIABLE", "METHOD_ID_CONFLICT", mid)
        elif m["identity_status"] != "AVAILABLE" or mid is None:
            _record_failure(report, mr, "OWNERSHIP", "UNVERIFIABLE", "METHOD_ID_UNAVAILABLE", m["identity_status"])
        elif mid != expected_id or not name_ok:
            _record_failure(report, mr, "OWNERSHIP", "OWNERSHIP_ERROR", "METHOD_ID_OWNERSHIP_MISMATCH", mid)
        elif m["location_status"] != "AVAILABLE":
            _record_failure(report, mr, "POSITION", "UNVERIFIABLE", "METHOD_LOCATION_UNAVAILABLE", m["location_status"])
        else:
            try:
                method_slice = snapshots[f["file"]].extract(m)
                if fr.get("local_unescaped_methods_checkable") and "\\u" in method_slice:
                    fault("POSITION", "UNVERIFIABLE", "METHOD_RAW_ESCAPE_UNSUPPORTED",
                          "Opaque-file protocol only certifies methods without Unicode escapes", mr["record_pointer"])
                mr["status"] = "CHECKED" if m["evidence_occurrences"] else "NO_EVIDENCE"
            except ValidationFault as exc:
                _record_failure(report, mr, exc.issue["stage"], exc.issue["status"],
                                exc.issue["reason_code"], exc.issue["detail"])
            except ValueError as exc:
                _record_failure(report, mr, "RANGE", "INVALID_RANGE", "INVALID_METHOD_RANGE", str(exc))
        if m["evidence_omissions"]:
            _record_failure(report, mr, "EVIDENCE", "UNVERIFIABLE", "INDEX_EVIDENCE_OMISSIONS", str(m["evidence_omissions"]))
    for f, m, e, er in evidences:
        if er["status"] != TERMINAL_PENDING:
            continue
        mr, fr = method_rows[id(m)], file_rows[f["file"]]
        if mr["status"] not in {"CHECKED", "NO_EVIDENCE"}:
            er.update(status=mr["status"], reason_code=mr.get("reason_code", "METHOD_UNAVAILABLE"))
            continue
        try:
            if e["evidence_type"] not in EVIDENCE_TYPES:
                fault("EVIDENCE", "UNVERIFIABLE", "UNSUPPORTED_EVIDENCE_TYPE", e["evidence_type"])
            if any(e.get(k) is None for k in COORDS):
                fault("RANGE", "UNVERIFIABLE", "MISSING_EVIDENCE_RANGE", "Evidence has no complete range")
            try:
                actual_snippet = snapshots[f["file"]].extract(e)
            except ValueError as exc:
                fault("RANGE", "INVALID_RANGE", "INVALID_EVIDENCE_RANGE", str(exc))
            begin = (e["start_line"], e["start_column"])
            end = (e["end_line"], e["end_column"])
            if not ((m["start_line"], m["start_column"]) <= begin <= end
                    <= (m["end_line"], m["end_column"])):
                fault("OWNERSHIP", "OWNERSHIP_ERROR", "EVIDENCE_OUTSIDE_METHOD", "Range is outside owning method")
            computed_id = compute_evidence_id(f["file"], f["source_sha256"], m["method_id"],
                                              e["evidence_type"], [e[k] for k in COORDS])
            er["basis"].update(expected_evidence_id=e["evidence_id"], computed_evidence_id=computed_id)
            if computed_id != e["evidence_id"]:
                fault("IDENTIFIER", "EVIDENCE_ID_MISMATCH", "EVIDENCE_ID_MISMATCH", "Declared evidence_id does not match TASK 001 inputs")
            er["basis"].update(expected_source_sha256=fr["expected_source_sha256"],
                               actual_source_sha256=fr["actual_source_sha256"],
                               expected_snippet_sha256=sha256(e["source_snippet"].encode("utf-8")),
                               actual_snippet_sha256=sha256(actual_snippet.encode("utf-8")),
                               extracted_source_snippet=actual_snippet)
            if actual_snippet != e["source_snippet"]:
                fault("SNIPPET", "SNIPPET_MISMATCH", "EXACT_SNIPPET_MISMATCH", "Original substring differs from declared snippet")
            er["basis"]["checks_passed"] = ["INDEX_BINDING", "SAFE_PATH", "SOURCE_HASH", "PARSE_AND_IDENTITY",
                                               "METHOD_CONTAINMENT", "UTF16_RANGE", "EVIDENCE_ID", "EXACT_SNIPPET"]
            er.update(status="CANDIDATE_PASS", reason_code="CHECKS_PASSED_PENDING_BATCH")
        except ValidationFault as exc:
            _record_failure(report, er, exc.issue["stage"], exc.issue["status"],
                            exc.issue["reason_code"], exc.issue["detail"])


def validate(repo, index, meta=None):
    """Return a deterministic report, including expected input failures. Never edits input."""
    repo, index = Path(repo).absolute(), Path(index).absolute()
    meta = Path(meta).absolute() if meta is not None else index.with_name(index.name + ".meta.json")
    report = _new_report(repo, index, meta)
    try:
        _run(report, repo, index, meta)
    except ValidationFault as exc:
        report["issues"].append(exc.issue)
        if exc.issue["status"] == "INDEX_INTEGRITY_ERROR":
            report["index_integrity"]["status"] = "FAIL"
    except KeyboardInterrupt:
        report["report_status"] = "ABORTED"
        _issue(report, "EXECUTION", "UNVERIFIABLE", "EXECUTION_INTERRUPTED", "Validation interrupted")
    except Exception as exc:
        report["report_status"] = "ABORTED"
        _issue(report, "EXECUTION", "UNVERIFIABLE", "UNEXPECTED_EXECUTION_ERROR", type(exc).__name__ + ": " + str(exc))
    return _finish(report)


def validate_evidence_v2(repo, index, evidence_id, *, index_sha256, metadata_sha256, meta=None):
    """Pinned single-occurrence byte certification; legacy validate() is unchanged.

    PASS is conditional on the caller's independently trusted index/metadata pins.
    This does not independently parse ASTs, prove semantic method ownership, or
    upgrade repository/file verdicts. The full repository report is retained.
    """
    repo, index = Path(repo).absolute(), Path(index).absolute()
    meta = Path(meta).absolute() if meta is not None else index.with_name(index.name + ".meta.json")
    report = _new_report(repo, index, meta)
    report.update(report_schema_version="2.0", validator_version="evidence-local-v2")
    report["verification_scope"].update(protocol="evidence-local-v2", certification="PINNED_INDEX_SOURCE_BYTE_CORRESPONDENCE",
                                      caller_trusted_pins_required=True)
    try:
        if not isinstance(evidence_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", evidence_id):
            fault("IDENTIFIER", "UNVERIFIABLE", "INVALID_SELECTED_EVIDENCE_ID", "Expected one exact evidence_id")
        if (not isinstance(index_sha256, str) or not SHA.fullmatch(index_sha256)
                or not isinstance(metadata_sha256, str) or not SHA.fullmatch(metadata_sha256)
                or sha256(index.read_bytes()) != index_sha256 or sha256(meta.read_bytes()) != metadata_sha256):
            fault("INTEGRITY", "INDEX_INTEGRITY_ERROR", "PINNED_INPUT_HASH_MISMATCH", "Caller pins must match exact input bytes")
        _run(report, repo, index, meta, local_protocol=True)
        if (report["inputs"].get("index_sha256") != index_sha256
                or report["inputs"].get("metadata_sha256") != metadata_sha256):
            fault("INTEGRITY", "INDEX_INTEGRITY_ERROR", "PINNED_INPUT_HASH_MISMATCH", "Inputs changed during validation")
        # Only completed per-occurrence checks qualify. Any fatal preflight above
        # leaves pending rows uncertified, including malformed/forged ownership.
        for row in report["evidence_results"]:
            if row["status"] == "CANDIDATE_PASS":
                row.update(status="VERIFIED", reason_code="ALL_LOCAL_SOURCE_CHECKS_PASSED")
    except ValidationFault as exc:
        report["issues"].append(exc.issue)
        if exc.issue["status"] == "INDEX_INTEGRITY_ERROR":
            report["index_integrity"]["status"] = "FAIL"
    except (OSError, ValueError, UnicodeError, TypeError, KeyError) as exc:
        _issue(report, "EXECUTION", "UNVERIFIABLE", "LOCAL_VALIDATION_ERROR", str(exc))
    report = _finish(report)
    matches = [row for row in report["evidence_results"] if row["evidence_id"] == evidence_id]
    passed = len(matches) == 1 and matches[0]["status"] == "VERIFIED"
    report["selected_evidence"] = {"evidence_id":evidence_id, "verdict":"PASS" if passed else "FAIL",
        "status":matches[0]["status"] if len(matches)==1 else "UNVERIFIABLE",
        "reason_code":matches[0]["reason_code"] if len(matches)==1 else "SELECTED_EVIDENCE_NOT_UNIQUE_OR_MISSING"}
    return report


def report_bytes(report):
    return (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _write_report(output, report, repo, inputs):
    output = Path(output).absolute()
    resolved = output.resolve(strict=False)
    root = Path(repo).resolve(strict=False)
    if resolved == root or root in resolved.parents:
        raise ValueError("Report output must be outside the target repository")
    if any(resolved == Path(p).resolve(strict=False) for p in inputs):
        raise ValueError("Report output must not be an input")
    if output.exists() or output.is_symlink():
        raise FileExistsError("Report already exists: " + str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = report_bytes(report)
    # Exclusive creation protects existing files, including a race after exists().
    created = False
    try:
        with output.open("xb") as handle:
            created = True
            handle.write(payload)
    except Exception:
        if created:
            output.unlink(missing_ok=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--index", required=True)
    parser.add_argument("--meta")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    report = validate(args.repo, args.index, args.meta)
    try:
        if args.out:
            metadata = args.meta or str(args.index) + ".meta.json"
            _write_report(args.out, report, args.repo, [args.index, metadata])
            print("REPORT_WRITTEN " + str(Path(args.out).absolute()), file=sys.stderr)
        else:
            sys.stdout.buffer.write(report_bytes(report))
    except (OSError, ValueError, UnicodeError) as exc:
        print("OUTPUT_ERROR: " + str(exc), file=sys.stderr)
        return 2
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
