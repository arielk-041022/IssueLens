"""Offline tests. Fixture changes never touch the two real Java repositories."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "src/evidence_validator.py"
SPEC = importlib.util.spec_from_file_location("task002_validator", MODULE_PATH)
ev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ev)

SOURCE = ('package p;\nclass A {\n  @Deprecated\n  void f() {\n'
          '    sink("x"); sink("x");\n  }\n}\n')


def fixture_id(file_path, source_hash, method_id, evidence_type, coords):
    # Independent ASCII fixture oracle. Unicode serializer correctness uses Java golden vectors below.
    items = [file_path, source_hash, method_id, evidence_type, *coords]
    raw = ("[ " + json.dumps(items)[1:-1] + " ]").encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class EvidenceValidatorTest(unittest.TestCase):
    def setUp(self):
        base = os.environ.get("TASK002_TEST_ROOT")
        if base:
            Path(base).mkdir(parents=True, exist_ok=True)
        # Keep fixtures and subprocess reports, including for failures.
        self.temp = Path(tempfile.mkdtemp(prefix="case-", dir=base))
        (self.temp / "test-id.txt").write_text(self.id(), encoding="utf-8")
        self.report_count = 0
        self.cli_count = 0
        self.repo = self.temp / "repo"
        self.path = "src/main/java/p/A.java"
        self.source_path = self.repo / self.path
        self.source_path.parent.mkdir(parents=True)
        self.source_path.write_bytes(SOURCE.encode("utf-8"))
        self.index_path = self.temp / "index.v2.json"
        self.meta_path = self.temp / "index.v2.json.meta.json"
        self.method = {
            "name": "f", "signature": "f()", "qualified_class_name": "p.A",
            "method_id": "java-source-v1:p.A#f()", "identity_status": "AVAILABLE",
            "location_status": "AVAILABLE", "line": 3,
            "start_line": 3, "start_column": 3, "end_line": 6, "end_column": 3,
            "has_body": True, "annotations": [], "strings": ["x"], "calls": ["sink"],
            "evidence_omissions": 0, "evidence_occurrences": [],
        }
        self.file = {"file": self.path, "package": "p", "classes": [{"name": "A", "methods": [self.method]}],
                     "source_sha256": hashlib.sha256(self.source_path.read_bytes()).hexdigest(),
                     "parse_status": "PARSED", "parse_problems": [], "limitations": []}
        for typ, coords, snippet in [
            ("ANNOTATION", (3, 3, 3, 13), "@Deprecated"),
            ("METHOD_CALL", (5, 5, 5, 13), 'sink("x")'),
            ("STRING_LITERAL", (5, 10, 5, 12), '"x"'),
            ("METHOD_CALL", (5, 16, 5, 24), 'sink("x")'),
            ("STRING_LITERAL", (5, 21, 5, 23), '"x"'),
        ]:
            self.method["evidence_occurrences"].append({
                "evidence_id": fixture_id(self.path, self.file["source_sha256"], self.method["method_id"], typ, coords),
                "evidence_type": typ, "file_path": self.path,
                **dict(zip(ev.COORDS, coords)), "source_snippet": snippet})
        self.index = [self.file]
        self.save()

    def save(self, recompute_ids=False):
        if recompute_ids:
            for f in self.index:
                for c in f["classes"]:
                    for m in c["methods"]:
                        for e in m["evidence_occurrences"]:
                            e["evidence_id"] = fixture_id(e["file_path"], f["source_sha256"],
                                                           m["method_id"], e["evidence_type"],
                                                           [e[k] for k in ev.COORDS])
        raw = json.dumps(self.index, ensure_ascii=False, indent=2).encode("utf-8")
        self.index_path.write_bytes(raw)
        methods = [m for f in self.index for c in f["classes"] for m in c["methods"]]
        conflicts = sorted({"java-source-v1:" + m["qualified_class_name"] + "#" + m["signature"]
                            for m in methods if m["identity_status"] == "CONFLICT"})
        self.meta = {
            "schema_version": "2.0", "position_convention": ev.POSITION,
            "method_id_scheme": "java-source-v1", "parser_version": "3.26.4",
            "language_level": "BLEEDING_EDGE", "source_encoding": "UTF-8",
            "preprocess_unicode_escapes": False, "source_roots": ["src/main/java"],
            "index_file": self.index_path.name, "index_sha256": hashlib.sha256(raw).hexdigest(),
            "files_total": len(self.index), "methods_total": len(methods),
            "evidence_occurrences": sum(len(m["evidence_occurrences"]) for m in methods),
            "identity_unavailable_methods": sum(m["identity_status"] != "AVAILABLE" for m in methods),
            "location_unavailable_methods": sum(m["location_status"] != "AVAILABLE" for m in methods),
            "identity_conflict_groups": len(conflicts),
            "identity_conflict_methods": sum(m["identity_status"] == "CONFLICT" for m in methods),
            "conflicting_method_ids": conflicts,
            "parse_counts": {k: sum(f["parse_status"] == k for f in self.index) for k in ev.PARSE_STATES},
        }
        self.save_meta()

    def save_meta(self):
        self.meta_path.write_text(json.dumps(self.meta), encoding="utf-8")

    def validate(self):
        return ev.validate(self.repo, self.index_path)

    def rejected(self, reason=None, status=None):
        report = self.validate()
        self.assertEqual("COMPLETE", report["report_status"])
        self.assertEqual("FAIL", report["overall_verdict"])
        self.assertNotEqual(0, report["exit_code"])
        self.assertEqual(0, report["summary"]["verified"])
        self.assertFalse(any(e["status"] == "VERIFIED" for e in report["evidence_results"]))
        if reason:
            self.assertIn(reason, [i["reason_code"] for i in report["issues"]])
        if status:
            self.assertIn(status, [i["status"] for i in report["issues"]])
        self.report_count += 1
        (self.temp / ("failure-report-%03d.json" % self.report_count)).write_bytes(ev.report_bytes(report))
        return report

    def cli(self, out=None):
        command = [sys.executable, "-B", str(MODULE_PATH), "--repo", str(self.repo), "--index", str(self.index_path)]
        if out is not None:
            command += ["--out", str(out)]
        proc = subprocess.run(command, capture_output=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.cli_count += 1
        (self.temp / ("cli-%03d-stdout.log" % self.cli_count)).write_bytes(proc.stdout)
        (self.temp / ("cli-%03d-stderr.log" % self.cli_count)).write_bytes(proc.stderr)
        return proc

    def test_valid_three_types_and_repeated_occurrences(self):
        report = self.validate()
        self.assertEqual("PASS", report["overall_verdict"])
        self.assertEqual("COMPLETE", report["report_status"])
        self.assertEqual(0, report["exit_code"])
        self.assertEqual(5, report["summary"]["verified"])
        self.assertEqual(5, len({e["evidence_id"] for e in report["evidence_results"]}))
        self.assertFalse(report["verification_scope"]["independent_ast_parse"])
        for e in report["evidence_results"]:
            self.assertEqual(e["basis"]["expected_evidence_id"], e["basis"]["computed_evidence_id"])
            self.assertEqual(e["basis"]["expected_snippet_sha256"], e["basis"]["actual_snippet_sha256"])

    def test_jackson_golden_ascii(self):
        data = ev.evidence_id_bytes("src/main/java/p/A.java", "a" * 64,
                                    "java-source-v1:p.A#f(String)", "METHOD_CALL", [3, 5, 3, 13])
        self.assertEqual('sha256:9f0dda2618de57b980401deec5d8864a9d3952998c873844e09bf082515284d3',
                         "sha256:" + hashlib.sha256(data).hexdigest())
        self.assertTrue(data.startswith(b'[ "src/'))
        self.assertTrue(data.endswith(b'3, 13 ]'))

    def test_jackson_golden_bmp_and_supplementary(self):
        data = ev.evidence_id_bytes("src/main/java/中文😀/A.java", "a" * 64,
                                    "java-source-v1:p.A#f(String)", "METHOD_CALL", [3, 5, 3, 13])
        self.assertIn(b"\\uD83D\\uDE00", data)
        self.assertEqual("sha256:a47199eb731099d1a7e32866cdef1414301f8007d088e0c22212f045f4da6f5f",
                         "sha256:" + hashlib.sha256(data).hexdigest())

    def test_jackson_golden_escaping(self):
        data = ev.evidence_id_bytes('src/main/java/q"\\\n\t\x01.java', "a" * 64,
                                    "java-source-v1:p.A#f(String)", "METHOD_CALL", [3, 5, 3, 13])
        self.assertEqual("sha256:9b6ebb74102cd074f5fb6511d1e93db77ea8c39a14f7db037f56f887619f35e5",
                         "sha256:" + hashlib.sha256(data).hexdigest())
        with self.assertRaises(UnicodeError):
            ev.jackson_string("\ud800")

    def test_tampered_evidence_id(self):
        self.method["evidence_occurrences"][0]["evidence_id"] = "sha256:" + "0" * 64
        self.save()
        self.rejected("EVIDENCE_ID_MISMATCH", "EVIDENCE_ID_MISMATCH")

    def test_duplicate_evidence_id_rejects_all_copies(self):
        self.method["evidence_occurrences"].append(copy.deepcopy(self.method["evidence_occurrences"][0]))
        self.save()
        report = self.rejected("DUPLICATE_EVIDENCE_ID")
        self.assertEqual(2, report["summary"]["evidence_status_counts"]["DUPLICATE_EVIDENCE_ID"])

    def test_evidence_file_ownership_mismatch(self):
        self.method["evidence_occurrences"][0]["file_path"] = "src/main/java/p/B.java"
        self.save()
        self.rejected("EVIDENCE_FILE_OWNERSHIP_MISMATCH", "INVALID_PATH")

    def test_evidence_outside_owner_method(self):
        self.method.update(start_line=4, start_column=3)
        self.save()
        self.rejected("EVIDENCE_OUTSIDE_METHOD", "OWNERSHIP_ERROR")

    def test_method_id_duplicate_without_declared_conflict(self):
        duplicate = copy.deepcopy(self.method)
        duplicate["evidence_occurrences"] = []
        self.file["classes"][0]["methods"].append(duplicate)
        self.save()
        self.rejected("METHOD_ID_CONFLICT")

    def test_declared_method_conflict_is_unverifiable(self):
        self.method.update(identity_status="CONFLICT", method_id=None, evidence_occurrences=[])
        self.save()
        self.rejected("METHOD_ID_UNAVAILABLE", "UNVERIFIABLE")

    def test_method_id_does_not_match_owner(self):
        self.method["method_id"] = "java-source-v1:p.Other#f()"
        self.save()
        self.rejected("METHOD_ID_OWNERSHIP_MISMATCH", "OWNERSHIP_ERROR")

    def test_evidence_id_bound_to_owner_not_just_range(self):
        self.method.update(name="g", signature="g()", method_id="java-source-v1:p.A#g()")
        self.save()  # preserve old evidence IDs
        self.rejected("EVIDENCE_ID_MISMATCH")

    def test_main_bytes_changed_even_whitespace_breaks_binding(self):
        self.index_path.write_bytes(self.index_path.read_bytes() + b" ")
        self.rejected("INDEX_HASH_MISMATCH", "INDEX_INTEGRITY_ERROR")

    def test_corrupt_main_json_produces_structured_failure_report(self):
        self.index_path.write_bytes(b"{not JSON")
        out = self.temp / "corrupt.validation.json"
        proc = self.cli(out)
        self.assertEqual(2, proc.returncode)
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual("COMPLETE", report["report_status"])
        self.assertEqual("FAIL", report["overall_verdict"])
        self.assertEqual("INDEX_READ", report["issues"][0]["stage"])
        self.assertEqual("INDEX_READ_OR_JSON_ERROR", report["issues"][0]["reason_code"])
        self.assertEqual(0, report["summary"]["verified"])

    def test_illegal_path_produces_structured_failure_report(self):
        self.file["file"] = "../outside.java"
        self.save()
        out = self.temp / "path.validation.json"
        proc = self.cli(out)
        self.assertEqual(2, proc.returncode)
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual("COMPLETE", report["report_status"])
        self.assertEqual("FAIL", report["overall_verdict"])
        self.assertEqual("PATH", report["issues"][0]["stage"])
        self.assertEqual(0, report["summary"]["verified"])

    def test_missing_v2_sidecar_is_not_legacy(self):
        self.meta_path.unlink()
        self.rejected("METADATA_READ_OR_JSON_ERROR", "INDEX_INTEGRITY_ERROR")

    def test_duplicate_json_keys_and_nonstandard_numbers(self):
        for raw in (b'{"file":1,"file":2}', b'[NaN]', b'[Infinity]'):
            with self.subTest(raw=raw):
                self.index_path.write_bytes(raw)
                self.rejected("INDEX_READ_OR_JSON_ERROR")

    def test_wrong_metadata_hash_name_and_counts(self):
        for key, value in (("index_sha256", "0" * 64), ("index_file", "other.json"),
                           ("methods_total", 99), ("files_total", True),
                           ("parse_counts", {"PARSED": True, "PARTIAL": 0, "FAILED": 0, "READ_ERROR": 0})):
            with self.subTest(key=key):
                self.save()
                self.meta[key] = value
                self.save_meta()
                self.rejected(status="INDEX_INTEGRITY_ERROR")

    def test_unknown_configuration_is_unverifiable(self):
        for key, value in (("schema_version", "99"), ("position_convention", "codepoints"),
                           ("preprocess_unicode_escapes", True)):
            with self.subTest(key=key):
                self.save()
                self.meta[key] = value
                self.save_meta()
                self.rejected("UNSUPPORTED_INDEX_CONFIGURATION", "UNVERIFIABLE")

    def test_invalid_schema(self):
        self.method["calls"] = "sink"
        self.save()
        self.rejected("INVALID_INDEX_SCHEMA", "INDEX_INTEGRITY_ERROR")

    def test_stale_source_even_if_snippets_still_exist(self):
        self.source_path.write_bytes(self.source_path.read_bytes() + b"// changed\n")
        out = self.temp / "stale.validation.json"
        proc = self.cli(out)
        self.assertEqual(1, proc.returncode)
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(5, report["summary"]["evidence_status_counts"]["STALE_SOURCE"])
        self.assertEqual(0, report["summary"]["verified"])
        self.assertEqual("COMPLETE", report["report_status"])

    def test_missing_source(self):
        self.source_path.unlink()
        self.rejected("SOURCE_NOT_FOUND", "MISSING_SOURCE")

    def test_paths_rejected_before_any_source_open(self):
        invalid = ["../A.java", "/src/main/java/A.java", "C:/x.java", "C:x.java", "//server/share/A.java",
                   "src\\main\\java\\A.java", "src/main/java/../A.java", "src/main/java//A.java",
                   "src/main/java/A.java:stream", "src/main/java/CON.java", "src/main/java/NUL",
                   "src/main/java/A.java.", "src/main/java/A.java ", "src/main/java/./A.java",
                   "src/main/java/A\x00.java", "src/test/java/A.java", "src/main/java/COM¹.java"]
        invalid += ["src/main/java/AUX .java", "src/main/java/CONOUT$.java", "src/main/java/A.txt"]
        for path in invalid:
            with self.subTest(path=path):
                self.file["file"] = path
                self.save()
                with mock.patch.object(ev, "_read_source", side_effect=AssertionError("Source opened")):
                    self.rejected(status="INVALID_PATH")

    def test_symlink_or_junction_source_is_rejected(self):
        outside = self.temp / "outside"
        outside.mkdir()
        (outside / "A.java").write_text(SOURCE, encoding="utf-8")
        link = self.repo / "src/main/java/link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                self.fail("Cannot create symlink for path safety test")
            proc = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
            (self.temp / "junction-creation.log").write_bytes(proc.stdout + proc.stderr)
            self.assertEqual(0, proc.returncode, "Cannot exercise reparse-point protection")
        self.addCleanup(lambda: os.rmdir(link) if os.name == "nt" and not link.is_symlink() else link.unlink())
        self.file["file"] = "src/main/java/link/A.java"
        self.save()
        self.rejected("REPARSE_POINT_REJECTED", "INVALID_PATH")

    def test_duplicate_file_path(self):
        self.index.append(copy.deepcopy(self.file))
        self.save()
        self.rejected("DUPLICATE_FILE_PATH", "INVALID_PATH")

    def test_invalid_evidence_ranges(self):
        evidence = self.method["evidence_occurrences"][1]
        original = {k: evidence[k] for k in ev.COORDS}
        for changes in ({"start_line": 0}, {"start_column": -1}, {"start_column": True},
                        {"start_line": 99}, {"end_column": 999}, {"start_column": 14, "end_column": 5},
                        {"start_line": "5"}, {"start_line": 6, "end_line": 5}):
            with self.subTest(changes=changes):
                evidence.update(original)
                evidence.update(changes)
                self.save()
                self.rejected("INVALID_EVIDENCE_RANGE", "INVALID_RANGE")

    def test_missing_range_is_unverifiable(self):
        del self.method["evidence_occurrences"][0]["start_line"]
        self.save()
        self.rejected("MISSING_EVIDENCE_RANGE", "UNVERIFIABLE")

    def test_invalid_method_range(self):
        self.method["end_line"] = 999
        self.save()
        self.rejected("INVALID_METHOD_RANGE", "INVALID_RANGE")

    def test_snippet_mismatch_with_correct_range_and_id(self):
        self.method["evidence_occurrences"][0]["source_snippet"] = "@Other"
        self.save()
        self.rejected("EXACT_SNIPPET_MISMATCH", "SNIPPET_MISMATCH")

    def test_source_lines_newlines_tab_and_unicode(self):
        for newline in ("\n", "\r\n", "\r"):
            with self.subTest(newline=newline):
                text = 'a' + newline + '\t调用("😀中文");' + newline + '  end'
                lines = ev.SourceLines(text)
                self.assertEqual('\t调用("😀中文");', lines.extract(dict(zip(ev.COORDS, [2, 1, 2, 12]))))
                self.assertEqual('"😀中文"', lines.extract(dict(zip(ev.COORDS, [2, 5, 2, 10]))))
                self.assertEqual('a' + newline + '\t调用("😀中文");' + newline + '  end',
                                 lines.extract(dict(zip(ev.COORDS, [1, 1, 3, 5]))))

    def test_surrogate_split_and_newline_endpoint_rejected(self):
        lines = ev.SourceLines('😀x\r\ny\n')
        for coords in ([1, 1, 1, 1], [1, 2, 1, 2], [1, 4, 2, 1], [1, 1, 1, 4], [3, 1, 3, 1]):
            with self.subTest(coords=coords), self.assertRaises(ValueError):
                lines.extract(dict(zip(ev.COORDS, coords)))

    def test_unicode_separators_do_not_create_java_lines(self):
        lines = ev.SourceLines("x\u2028\u2029\x85y\nz")
        self.assertEqual(2, len(lines.lines))
        self.assertEqual("x\u2028\u2029\x85y", lines.extract(dict(zip(ev.COORDS, [1, 1, 1, 5]))))

    def test_bom_and_unicode_escape_fail_closed(self):
        for prefix in ("\ufeff", "// " + "\\" + "u0061\n"):
            with self.subTest(prefix=prefix):
                self.source_path.write_bytes((prefix + SOURCE).encode("utf-8"))
                self.file["source_sha256"] = hashlib.sha256(self.source_path.read_bytes()).hexdigest()
                self.save(recompute_ids=True)
                self.rejected("UNSUPPORTED_RAW_MAPPING", "UNVERIFIABLE")

    def test_invalid_utf8_and_missing_source_hash(self):
        self.source_path.write_bytes(b"\xff")
        self.file["source_sha256"] = hashlib.sha256(b"\xff").hexdigest()
        self.save()
        self.rejected("INVALID_UTF8_SOURCE")
        self.file["source_sha256"] = None
        self.save()
        self.rejected("MISSING_SOURCE_HASH")

    def test_parse_states_not_misreported_as_verified(self):
        for state in ("PARTIAL", "FAILED", "READ_ERROR"):
            with self.subTest(state=state):
                self.file["parse_status"] = state
                self.save()
                self.rejected("PARSE_NOT_AVAILABLE", "UNVERIFIABLE")

    def test_unavailable_identity_location_and_omissions(self):
        for key, value, reason in (("identity_status", "UNSUPPORTED_LOCAL_TYPE", "METHOD_ID_UNAVAILABLE"),
                                   ("location_status", "UNAVAILABLE_MAPPING", "METHOD_LOCATION_UNAVAILABLE"),
                                   ("evidence_omissions", 1, "INDEX_EVIDENCE_OMISSIONS")):
            with self.subTest(key=key):
                previous = self.method[key]
                self.method[key] = value
                self.save()
                self.rejected(reason)
                self.method[key] = previous

    def test_unknown_or_llm_evidence_not_source_verification(self):
        self.method["evidence_occurrences"][0]["evidence_type"] = "LLM_INFERENCE"
        self.save()
        self.rejected("UNSUPPORTED_EVIDENCE_TYPE", "UNVERIFIABLE")

    def test_file_hash_uses_bytes_and_preserves_crlf(self):
        self.source_path.write_bytes(SOURCE.replace("\n", "\r\n").encode("utf-8"))
        self.rejected("SOURCE_HASH_MISMATCH")
        self.file["source_sha256"] = hashlib.sha256(self.source_path.read_bytes()).hexdigest()
        self.save(recompute_ids=True)
        self.assertEqual(5, self.validate()["summary"]["verified"])

    def test_multi_file_failure_suppresses_otherwise_valid_evidence(self):
        other = copy.deepcopy(self.file)
        other["file"] = "src/main/java/p/B.java"
        other["classes"][0]["name"] = "B"
        m = other["classes"][0]["methods"][0]
        m.update(qualified_class_name="p.B", method_id="java-source-v1:p.B#f()")
        for e in m["evidence_occurrences"]:
            e["file_path"] = other["file"]
        self.index.append(other)
        self.save(recompute_ids=True)
        report = self.rejected("SOURCE_NOT_FOUND")
        self.assertIn("BATCH_FAIL_CLOSED", [e["reason_code"] for e in report["evidence_results"]])

    def test_legacy_and_empty_indexes_are_not_vacuous_passes(self):
        self.meta_path.unlink()
        legacy = [{"file": self.path, "classes": [{"name": "A", "methods": [{"name": "f", "line": 4,
                   "annotations": [], "strings": [], "calls": []}]}]}]
        self.index_path.write_text(json.dumps(legacy), encoding="utf-8")
        self.rejected("LEGACY_INDEX_NO_EVIDENCE")
        self.index = []
        self.save()
        self.rejected("NO_EVIDENCE")

    def test_available_method_without_occurrences_is_not_a_verified_method(self):
        self.method["evidence_occurrences"] = []
        self.save()
        report = self.rejected("NO_EVIDENCE")
        self.assertEqual("NO_EVIDENCE", report["method_results"][0]["status"])

    def test_execution_status_is_separate_from_verdict(self):
        self.method["evidence_occurrences"][0]["source_snippet"] = "wrong"
        self.save()
        self.rejected("EXACT_SNIPPET_MISMATCH")
        with mock.patch.object(ev, "_read_source", side_effect=RuntimeError("injected execution failure")):
            report = self.validate()
        self.assertEqual("ABORTED", report["report_status"])
        self.assertEqual("FAIL", report["overall_verdict"])
        self.assertEqual(0, report["summary"]["verified"])
        self.assertEqual(2, report["exit_code"])

    def test_report_determinism_and_readonly_inputs(self):
        before = {p: p.read_bytes() for p in (self.index_path, self.meta_path, self.source_path)}
        a, b = ev.report_bytes(self.validate()), ev.report_bytes(self.validate())
        self.assertEqual(a, b)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_success_cli_stdout_and_report_output(self):
        proc = self.cli()
        self.assertEqual(0, proc.returncode)
        self.assertEqual("PASS", json.loads(proc.stdout)["overall_verdict"])
        out = self.temp / "success.json"
        self.assertEqual(0, self.cli(out).returncode)
        self.assertEqual(5, json.loads(out.read_bytes())["summary"]["verified"])

    def test_cli_existing_output_is_never_overwritten(self):
        out = self.temp / "existing.json"
        out.write_bytes(b"sentinel")
        proc = self.cli(out)
        self.assertEqual(2, proc.returncode)
        self.assertIn(b"OUTPUT_ERROR", proc.stderr)
        self.assertEqual(b"sentinel", out.read_bytes())

    def test_cli_report_inside_repository_or_input_is_rejected(self):
        for out in (self.repo / "new-report.json", self.index_path, self.meta_path):
            with self.subTest(out=out):
                before = out.read_bytes() if out.exists() else None
                proc = self.cli(out)
                self.assertEqual(2, proc.returncode)
                self.assertEqual(before, out.read_bytes() if out.exists() else None)

    def test_cli_unwritable_output_reports_stderr_and_nonzero(self):
        parent = self.temp / "not-directory"
        parent.write_bytes(b"sentinel")
        proc = self.cli(parent / "report.json")
        self.assertEqual(2, proc.returncode)
        self.assertIn(b"OUTPUT_ERROR", proc.stderr)
        self.assertEqual(b"sentinel", parent.read_bytes())

    def test_malformed_status_containers_are_completed_input_failures(self):
        for key, value in (("identity_status", []), ("location_status", {})):
            with self.subTest(key=key):
                previous = self.method[key]
                self.method[key] = value
                self.save()
                self.rejected("INVALID_INDEX_SCHEMA", "INDEX_INTEGRITY_ERROR")
                self.method[key] = previous

    def test_unpaired_surrogate_in_json_has_structured_report(self):
        self.index_path.write_bytes(b'{"bad":"\\ud800"}')
        output = self.temp / "surrogate.validation.json"
        self.assertEqual(2, self.cli(output).returncode)
        report = json.loads(output.read_bytes())
        self.assertEqual("COMPLETE", report["report_status"])
        self.assertEqual(0, report["summary"]["verified"])
        self.assertEqual("INDEX_READ_OR_JSON_ERROR", report["issues"][0]["reason_code"])

    def test_source_read_permission_failure_is_completed(self):
        original = Path.open
        def denied(path, *args, **kwargs):
            if path == self.source_path:
                raise PermissionError("injected read permission denial")
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "open", denied):
            self.rejected("SOURCE_READ_ERROR", "UNVERIFIABLE")

    def test_source_changed_during_read_is_not_verified(self):
        original = Path.stat
        def changed(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path == self.source_path and not kwargs:
                altered = mock.Mock(wraps=value)
                altered.st_dev = value.st_dev
                altered.st_ino = value.st_ino
                altered.st_size = value.st_size + 1
                altered.st_mtime_ns = value.st_mtime_ns
                return altered
            return value
        with mock.patch.object(Path, "stat", changed):
            self.rejected("SOURCE_CHANGED_DURING_READ", "UNVERIFIABLE")

    def test_interruption_has_aborted_report_without_verified(self):
        with mock.patch.object(ev, "_read_source", side_effect=KeyboardInterrupt):
            report = self.validate()
        self.assertEqual("ABORTED", report["report_status"])
        self.assertEqual("FAIL", report["overall_verdict"])
        self.assertEqual(0, report["summary"]["verified"])
        self.assertEqual("EXECUTION_INTERRUPTED", report["issues"][0]["reason_code"])

    def test_unsupported_source_root_does_not_open_any_source(self):
        self.meta["source_roots"] = ["../outside"]
        self.save_meta()
        with mock.patch.object(ev, "_read_source", side_effect=AssertionError("Source opened")):
            self.rejected("UNSUPPORTED_SOURCE_ROOTS", "INVALID_PATH")

    def test_missing_main_also_writes_failure_report(self):
        self.index_path.unlink()
        output = self.temp / "missing.validation.json"
        self.assertEqual(2, self.cli(output).returncode)
        report = json.loads(output.read_bytes())
        self.assertEqual("COMPLETE", report["report_status"])
        self.assertEqual("FAIL", report["overall_verdict"])
        self.assertEqual("INDEX_READ_OR_JSON_ERROR", report["issues"][0]["reason_code"])

    def test_full_unicode_path_id_and_utf16_snippet_validation(self):
        text = SOURCE.replace('sink("x"); sink("x");', 'sink("😀"); sink("😀");')
        self.path = "src/main/java/p/中文😀.java"
        self.source_path.unlink()
        self.source_path = self.repo / self.path
        self.source_path.write_bytes(text.encode("utf-8"))
        self.file["file"] = self.path
        self.file["source_sha256"] = hashlib.sha256(self.source_path.read_bytes()).hexdigest()
        new_ranges = [(3, 3, 3, 13), (5, 5, 5, 14), (5, 10, 5, 13), (5, 17, 5, 26), (5, 22, 5, 25)]
        for e, coords in zip(self.method["evidence_occurrences"], new_ranges):
            e.update(file_path=self.path, **dict(zip(ev.COORDS, coords)))
            e["source_snippet"] = e["source_snippet"].replace('"x"', '"😀"')
            values = [self.path, self.file["source_sha256"], self.method["method_id"], e["evidence_type"], *coords]
            # Independent fixture oracle for this known supplementary code point; Java goldens
            # pin the general serializer separately.
            serialized = ("[ " + json.dumps(values, ensure_ascii=False)[1:-1] + " ]").replace("😀", "\\uD83D\\uDE00")
            e["evidence_id"] = "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        self.save()
        report = self.validate()
        self.assertEqual("PASS", report["overall_verdict"])
        self.assertEqual(5, report["summary"]["verified"])


    def unicode_source(self, extra, limitations=()):
        raw = SOURCE.replace('class A {', 'class A { ' + extra)
        self.source_path.write_bytes(raw.encode('utf-8'))
        self.file['source_sha256'] = hashlib.sha256(self.source_path.read_bytes()).hexdigest()
        self.file['limitations'] = list(limitations)
        self.save(recompute_ids=True)

    def test_narrow_unicode_exact_char_literals_validate(self):
        for value in ('0000', 'FFFD'):
            with self.subTest(value=value):
                self.unicode_source("char c='" + '\\u' + value + "';")
                r = self.validate()
                self.assertEqual('PASS', r['overall_verdict'])
                self.assertEqual(5, r['summary']['verified'])
                self.assertFalse(r['verification_scope']['independent_ast_parse'])

    def test_narrow_unicode_real_trigger_expressions_validate(self):
        for raw in ("char replacementChar='\\uFFFD';", "char nullChar='\\u0000';",
                    "String nullString=String.valueOf('\\u0000');"):
            with self.subTest(raw=raw):
                self.unicode_source(raw)
                self.assertEqual('PASS', self.validate()['overall_verdict'])

    def test_narrow_unicode_unknown_and_qualification_fail_closed(self):
        for raw in ("'\\u0041'", "'\\ufffd'", "'\\uu0000'", "'\\u000a'", "'\\u0009'",
                    "'\\u00G0'", "'\\u000'", "'\\\\u0000'", "'\\\\\\u0000'",
                    '"\\u0000"', '"\\uFFFD\\u0000"', '"\\u005c\\u005a"', '\\u0009'):
            with self.subTest(raw=raw):
                self.unicode_source('Object x=' + raw + ';')
                self.rejected('UNSUPPORTED_RAW_MAPPING')

    def test_narrow_unicode_comment_and_string_spoofs_fail_closed(self):
        for raw in ("/* '\\u0000' */", "// '\\uFFFD'", 'String s="\'\\u0000\'";',
                    'String s="""\n\'\\u0000\'\n""";'):
            with self.subTest(raw=raw):
                self.unicode_source(raw)
                self.rejected('UNSUPPORTED_RAW_MAPPING')

    def test_narrow_unicode_mixed_unsafe_file_suppresses_verified(self):
        self.unicode_source("char c='\\u0000'; String s=\"\\u0041\";")
        self.rejected('UNSUPPORTED_RAW_MAPPING')

    def test_narrow_unicode_old_mapping_limitation_is_not_upgraded(self):
        self.unicode_source("char c='\\uFFFD';", ['UNICODE_ESCAPE_RAW_MAPPING_UNSUPPORTED'])
        self.rejected('INDEX_MAPPING_UNAVAILABLE')

    def test_narrow_unicode_still_checks_hash_and_snippet(self):
        self.unicode_source("char c='\\u0000';")
        self.method['evidence_occurrences'][0]['source_snippet'] = '@Bogus'
        self.save()
        self.rejected('EXACT_SNIPPET_MISMATCH')

    def test_narrow_unicode_raw_gate_crlf_tabs_and_supplementary_chars(self):
        for nl in ('\n', '\r\n', '\r'):
            raw = 'class A {' + nl + '\tvoid f(){sink("中文😀");char c=\'\\u0000\';}' + nl + '}'
            self.assertTrue(ev.unicode_literal_mapping_safe(raw))
        self.assertFalse(ev.unicode_literal_mapping_safe("class A {void \\u0066(){} char c='\\u0000';}"))


    def local(self, evidence=None, **kwargs):
        return ev.validate_evidence_v2(self.repo, self.index_path,
            evidence or self.method['evidence_occurrences'][0]['evidence_id'],
            index_sha256=kwargs.get('index_sha256', ev.sha256(self.index_path.read_bytes())),
            metadata_sha256=ev.sha256(self.meta_path.read_bytes()))

    def test_local_v2_success_has_explicit_limited_scope(self):
        r = self.local()
        self.assertEqual('PASS', r['selected_evidence']['verdict'])
        self.assertEqual('PASS', r['overall_verdict'])
        self.assertFalse(r['verification_scope']['independent_ast_parse'])
        self.assertEqual('evidence-local-v2', r['validator_version'])

    def test_local_v2_opaque_file_unescaped_method_and_legacy_isolation(self):
        self.unicode_source('String s="\\u0041";', ['UNICODE_ESCAPE_RAW_MAPPING_UNSUPPORTED'])
        old = self.validate()
        r = self.local()
        self.assertEqual('FAIL', old['overall_verdict'])
        self.assertEqual(0, old['summary']['verified'])
        self.assertEqual('PASS', r['selected_evidence']['verdict'])
        self.assertEqual('FAIL', r['overall_verdict'])
        self.assertEqual('UNVERIFIABLE', r['file_results'][0]['status'])
        self.assertEqual(5, r['summary']['verified'])

    def test_local_v2_other_occurrence_failure_does_not_certify_it(self):
        self.method['evidence_occurrences'][1]['source_snippet'] = 'forged'
        self.save()
        r = self.local()
        self.assertEqual('PASS', r['selected_evidence']['verdict'])
        self.assertEqual('FAIL', r['overall_verdict'])
        self.assertEqual('SNIPPET_MISMATCH', r['evidence_results'][1]['status'])

    def test_local_v2_pinned_index_tamper(self):
        self.assertEqual('FAIL', self.local(index_sha256='0'*64)['selected_evidence']['verdict'])

    def test_local_v2_stale_source_rejected(self):
        self.source_path.write_bytes((SOURCE+'// tampered').encode())
        r = self.local()
        self.assertEqual('FAIL', r['selected_evidence']['verdict'])
        self.assertIn('SOURCE_HASH_MISMATCH', [x['reason_code'] for x in r['issues']])

    def test_local_v2_dangerous_escapes_and_bom_rejected(self):
        for extra in ('String s="\\u000a";', '/* \\u002a/ */', 'Object \\u0066;'):
            with self.subTest(extra=extra):
                self.unicode_source(extra)
                self.assertEqual('FAIL', self.local()['selected_evidence']['verdict'])
        self.source_path.write_bytes(b'\xef\xbb\xbf'+SOURCE.encode())
        self.file['source_sha256'] = ev.sha256(self.source_path.read_bytes())
        self.save(recompute_ids=True)
        self.assertEqual('FAIL', self.local()['selected_evidence']['verdict'])

    def test_local_v2_escaped_method_rejected_even_if_available(self):
        self.source_path.write_bytes(SOURCE.replace('void f() {', 'void f() { /* \\u0041 */').encode())
        self.file['source_sha256'] = ev.sha256(self.source_path.read_bytes())
        self.save(recompute_ids=True)
        r = self.local()
        self.assertEqual('FAIL', r['selected_evidence']['verdict'])
        self.assertIn('METHOD_RAW_ESCAPE_UNSUPPORTED', [x['reason_code'] for x in r['issues']])

    def test_local_v2_range_id_ownership_still_rejected(self):
        e = self.method['evidence_occurrences'][0]
        for key,value in [('end_column',999), ('evidence_id','sha256:'+'a'*64), ('file_path','src/main/java/p/B.java')]:
            old = e[key]
            e[key] = value
            self.save()
            self.assertEqual('FAIL', self.local()['selected_evidence']['verdict'])
            e[key] = old
        self.save()

    def test_local_v2_unknown_evidence_is_not_success(self):
        self.assertEqual('FAIL',self.local('sha256:'+'0'*64)['selected_evidence']['verdict'])

    def test_local_v2_hard_mapping_limitation_still_rejected(self):
        self.unicode_source('String s="\\u0041";', ['SOURCE_TOKEN_MAPPING_UNAVAILABLE'])
        self.assertEqual('FAIL', self.local()['selected_evidence']['verdict'])

    def test_local_v2_direct_src_import_without_project_root(self):
        self.unicode_source('String s="\\u0041";', ['UNICODE_ESCAPE_RAW_MAPPING_UNSUPPORTED'])
        arguments = [str(self.repo), str(self.index_path), self.method['evidence_occurrences'][0]['evidence_id'],
                     ev.sha256(self.index_path.read_bytes()), ev.sha256(self.meta_path.read_bytes())]
        code = ('import sys,json; sys.path.insert(0,sys.argv[1]); import evidence_validator as ev; '
                'r=ev.validate_evidence_v2(sys.argv[2],sys.argv[3],sys.argv[4],index_sha256=sys.argv[5],metadata_sha256=sys.argv[6]); '
                'print(json.dumps(r["selected_evidence"]))')
        proc = subprocess.run([sys.executable, '-B', '-c', code, str(MODULE_PATH.parent), *arguments],
                              cwd=self.temp, capture_output=True)
        self.assertEqual(0,proc.returncode,proc.stderr.decode(errors='replace'))
        self.assertEqual('PASS',json.loads(proc.stdout)['verdict'])


if __name__ == "__main__":
    unittest.main()
