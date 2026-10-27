"""C2-A synthetic-only issuance tests; never uses real sources or permits."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
s = importlib.util.spec_from_file_location("c2_c1_helpers", ROOT / "tests/test_controlled_authorization.py")
c1 = importlib.util.module_from_spec(s); s.loader.exec_module(c1)
a = c1.auth


class RealIssuanceTest(unittest.TestCase):
    def setUp(self):
        self.h = c1.ControlledAuthorizationTest(methodName="runTest")
        self.h.setUp(); self.temp = self.h.temp

    def approval(self, issuer, purpose, subject):
        registry = a.read(issuer / "registry.json")
        body = dict(version="research-approval-v1", issuer_id=registry["issuer_id"], role="RESEARCH_LEAD",
                    decision="APPROVED", purpose=purpose, subject_sha256=subject,
                    record="SYNTHETIC TEST ONLY: not a real research authorization.")
        p = self.temp / (purpose + ".approval.json")
        a.put(p, dict(body=body, mac=a.sign(registry["key"], body)))
        a.put(issuer / "research-approval-pins.json", {purpose: a.file_hash(p)})
        return p

    def campaign(self):
        c = self.h.setup_campaign()
        body = copy.deepcopy(a.read(c["permit"])["body"])
        body.update(scope="REAL_OFFLINE_ONCE", run_id="c2-synthetic-issuance")
        return c, body

    def test_boolean_is_not_approval(self):
        with self.assertRaises((TypeError, ValueError, OSError)):
            a.research_approval(self.temp, True, "ISSUE_REAL", "0" * 64)

    def test_missing_trusted_pin_rejected(self):
        c, b = self.campaign()
        with self.assertRaises(FileNotFoundError):
            a.research_approval(c["issuer"], c["permit"], "ISSUE_REAL", a.digest(a.canonical(b)))

    def test_pinned_signed_approval_validates_without_issuance(self):
        issuer = self.temp / "approval-only"; a.init_issuer(issuer)
        p = self.approval(issuer, "QUALIFY_REAL", "a" * 64)
        self.assertEqual(a.research_approval(issuer, p, "QUALIFY_REAL", "a" * 64)["real_approval_pins"], {})

    def test_wrong_subject_rejected(self):
        issuer = self.temp / "issuer"; a.init_issuer(issuer)
        p = self.approval(issuer, "ISSUE_REAL", "a" * 64)
        with self.assertRaisesRegex(ValueError, "SCOPE_MISMATCH"):
            a.research_approval(issuer, p, "ISSUE_REAL", "b" * 64)

    def test_wrong_purpose_rejected(self):
        issuer = self.temp / "issuer"; a.init_issuer(issuer)
        p = self.approval(issuer, "QUALIFY_REAL", "a" * 64)
        with self.assertRaisesRegex(ValueError, "NOT_PINNED"):
            a.research_approval(issuer, p, "ISSUE_REAL", "a" * 64)

    def test_tampered_approval_rejected(self):
        issuer = self.temp / "issuer"; a.init_issuer(issuer)
        p = self.approval(issuer, "ISSUE_REAL", "a" * 64)
        p.write_bytes(p.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "NOT_PINNED"):
            a.research_approval(issuer, p, "ISSUE_REAL", "a" * 64)

    def test_invalid_signature_even_if_pinned(self):
        issuer = self.temp / "issuer"; a.init_issuer(issuer)
        p = self.approval(issuer, "ISSUE_REAL", "a" * 64)
        v = a.read(p); v["mac"] = "0" * 64; self.h.h.save(p, v)
        self.h.h.save(issuer / "research-approval-pins.json", {"ISSUE_REAL": a.file_hash(p)})
        with self.assertRaisesRegex(ValueError, "SIGNATURE"):
            a.research_approval(issuer, p, "ISSUE_REAL", "a" * 64)

    def test_wrong_role_even_with_signature(self):
        issuer = self.temp / "issuer"; r = a.init_issuer(issuer)
        p = self.approval(issuer, "ISSUE_REAL", "a" * 64)
        v = a.read(p); v["body"]["role"] = "MODEL"; v["mac"] = a.sign(r["key"], v["body"])
        self.h.h.save(p, v); self.h.h.save(issuer / "research-approval-pins.json", {"ISSUE_REAL": a.file_hash(p)})
        with self.assertRaisesRegex(ValueError, "SCOPE_MISMATCH"):
            a.research_approval(issuer, p, "ISSUE_REAL", "a" * 64)

    def test_runtime_rejects_other_python(self):
        with mock.patch.object(a.sys, "version_info", (3, 12)):
            with self.assertRaisesRegex(ValueError, "PYTHON310"):
                a.real_runtime()

    def test_fixture_cannot_qualify_as_real(self):
        c, _ = self.campaign()
        with mock.patch.object(a, "real_runtime"):
            with self.assertRaisesRegex(ValueError, "INVALID_REAL_COHORT"):
                a.qualify_real(c["issuer"], c["job"], self.temp / "no-real", self.temp / "missing")

    def test_unapproved_qualification_never_starts_subprocess(self):
        c, _ = self.campaign()
        ids = {v["case_id"] for v in c["cases"]}
        with mock.patch.object(a, "real_runtime"), mock.patch.object(a, "REAL_IDS", ids), \
             mock.patch.object(a.subprocess, "run") as process:
            with self.assertRaises(FileNotFoundError):
                a.qualify_real(c["issuer"], c["job"], self.temp / "no-real", self.temp / "missing")
            process.assert_not_called()

    def test_qualification_receipt_is_sanitized_and_preflight_is_separate(self):
        c, _ = self.campaign()
        approval = self.approval(c["issuer"], "QUALIFY_REAL", a.file_hash(c["job"]))
        ids = {v["case_id"] for v in c["cases"]}
        snapshots = {v["case_id"]: v["source_snapshot_id"] for v in c["cases"]}
        original_run = a.subprocess.run
        calls = []
        def synthetic_preflight(command, **kwargs):
            calls.append(command)
            result = original_run(command, **kwargs)
            out = Path(command[command.index("--out-dir") + 1])
            # Substitute only synthetic audit scope; no real artifacts are read.
            for i in range(4):
                p = out / (str(i) + ".audit.json"); v = a.read(p)
                v["snapshot_results"][0]["repo_id"] = "jhy/jsoup"
                self.h.h.save(p, v)
            return result
        with mock.patch.object(a, "real_runtime"), mock.patch.object(a, "REAL_IDS", ids), \
             mock.patch.object(a, "REAL_CASE_SNAPSHOTS", snapshots), \
             mock.patch.object(a.subprocess, "run", side_effect=synthetic_preflight):
            receipt = a.qualify_real(c["issuer"], c["job"], self.temp / "synthetic-real-qualification", approval)
        self.assertEqual(len(calls), 1); self.assertIn("-I", calls[0]); self.assertIn("-B", calls[0])
        text = a.canonical(receipt).decode()
        for f in c["fs"]:
            self.assertNotIn(str(f["gt"]), text)
        for v in receipt["body"]["cases"]:
            self.assertNotIn("gt_sidecar", v); self.assertNotIn("targets", v)

    def test_fixture_cannot_form_real_body(self):
        c, _ = self.campaign()
        with self.assertRaisesRegex(ValueError, "FIXED_REAL_CASES"):
            a.real_body(a.read(c["issuer"] / "registry.json"), "never", c["eligibility"], c["cases"], c["output"])

    def test_real_issuance_without_approval_never_writes(self):
        c, b = self.campaign(); p = self.temp / "not-issued.json"
        with mock.patch.object(a, "real_runtime"), mock.patch.object(a, "real_body", return_value=b):
            with self.assertRaises(FileNotFoundError):
                a.issue_real(c["issuer"], b["run_id"], c["eligibility"], c["cases"], c["output"], p, self.temp / "missing")
        self.assertFalse(p.exists())
        self.assertEqual(a.read(c["issuer"] / "registry.json")["real_approval_pins"], {})

    def test_synthetic_scope_issuance_and_replay(self):
        # Patch the real universe to fixture IDs and a synthetic body. This cannot
        # authorize the genuine four issues under the unpatched production code.
        c, b = self.campaign(); p = self.temp / "synthetic-scope-permit.json"
        approval = self.approval(c["issuer"], "ISSUE_REAL", a.digest(a.canonical(b)))
        with mock.patch.object(a, "real_runtime"), mock.patch.object(a, "real_body", return_value=b), \
             mock.patch.object(a, "REAL_IDS", {v["case_id"] for v in c["cases"]}):
            ph = a.issue_real(c["issuer"], b["run_id"], c["eligibility"], c["cases"], c["output"], p, approval)
            self.assertEqual(ph, a.file_hash(p))
            with self.assertRaises(FileExistsError):
                a.issue_real(c["issuer"], b["run_id"], c["eligibility"], c["cases"], c["output"], self.temp / "replay.json", approval)
        with self.assertRaisesRegex(ValueError, "REAL_RESEARCH_APPROVAL_ABSENT"):
            a.validate_shape(a.read(p), a.read(c["issuer"] / "registry.json")["key"], a.digest(a.canonical(b)))

    def test_failed_binding_revokes_pin(self):
        c, b = self.campaign(); p = self.temp / "rejected.json"
        approval = self.approval(c["issuer"], "ISSUE_REAL", a.digest(a.canonical(b)))
        with mock.patch.object(a, "real_runtime"), mock.patch.object(a, "real_body", return_value=b), \
             mock.patch.object(a, "REAL_IDS", {v["case_id"] for v in c["cases"]}), \
             mock.patch.object(a, "verify_disk", side_effect=ValueError("SYNTHETIC_TAMPER")):
            with self.assertRaisesRegex(ValueError, "SYNTHETIC_TAMPER"):
                a.issue_real(c["issuer"], b["run_id"], c["eligibility"], c["cases"], c["output"], p, approval)
        self.assertFalse(p.exists()); self.assertTrue(Path(str(p) + ".REJECTED").exists())
        self.assertNotIn(b["run_id"], a.read(c["issuer"] / "registry.json")["real_approval_pins"])


if __name__ == "__main__": unittest.main()
