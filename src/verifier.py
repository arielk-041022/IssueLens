# src/verifier.py
import argparse
import json
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def get_case(bench_path, case_id=None):
    bench = load_json(bench_path)
    if isinstance(bench, dict):
        cases = bench.get("cases") or bench.get("benchmark") or [bench]
    else:
        cases = bench

    if case_id:
        for c in cases:
            if c.get("id") == case_id:
                return c
        raise SystemExit(f"找不到 case id: {case_id}")

    return cases[0]


def run_command(command, cwd):
    print(f"[verifier] 执行: {command}")
    print(f"[verifier] cwd: {cwd}")

    start = time.time()
    proc = subprocess.run(
        command,
        cwd=cwd,
        shell=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    elapsed = time.time() - start

    print(f"[verifier] 退出码: {proc.returncode}, 耗时: {elapsed:.2f}s")

    if proc.stdout:
        print("\n--- stdout tail ---")
        print("\n".join(proc.stdout.splitlines()[-40:]))

    if proc.stderr:
        print("\n--- stderr tail ---")
        print("\n".join(proc.stderr.splitlines()[-40:]))

    return proc, elapsed


def parse_surefire(repo_path: Path):
    report_dir = repo_path / "target" / "surefire-reports"

    if not report_dir.exists():
        return {
            "found": False,
            "reason": f"{report_dir} 不存在",
            "suites": [],
            "testcases": [],
            "tests": 0,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
        }

    xmls = sorted(report_dir.glob("*.xml"))
    if not xmls:
        return {
            "found": False,
            "reason": f"{report_dir} 下没有 XML",
            "suites": [],
            "testcases": [],
            "tests": 0,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
        }

    total = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    suites = []
    testcases = []

    for xml in xmls:
        try:
            tree = ET.parse(xml)
            root = tree.getroot()
        except Exception as e:
            suites.append({"file": str(xml), "error": str(e)})
            continue

        nodes = [root] if root.tag == "testsuite" else root.findall("testsuite")

        for suite in nodes:
            attrs = suite.attrib
            s = {
                "name": attrs.get("name"),
                "tests": int(attrs.get("tests", 0)),
                "failures": int(attrs.get("failures", 0)),
                "errors": int(attrs.get("errors", 0)),
                "skipped": int(attrs.get("skipped", 0)),
                "time": attrs.get("time"),
                "file": str(xml),
            }
            suites.append(s)

            total["tests"] += s["tests"]
            total["failures"] += s["failures"]
            total["errors"] += s["errors"]
            total["skipped"] += s["skipped"]

            for tc in suite.findall("testcase"):
                status = "passed"
                message = ""

                failure = tc.find("failure")
                error = tc.find("error")
                skipped = tc.find("skipped")

                if failure is not None:
                    status = "failed"
                    message = failure.attrib.get("message", "") or (failure.text or "")[:500]
                elif error is not None:
                    status = "error"
                    message = error.attrib.get("message", "") or (error.text or "")[:500]
                elif skipped is not None:
                    status = "skipped"

                testcases.append({
                    "classname": tc.attrib.get("classname"),
                    "name": tc.attrib.get("name"),
                    "time": tc.attrib.get("time"),
                    "status": status,
                    "message": message,
                })

    return {
        "found": True,
        "report_dir": str(report_dir),
        "suites": suites,
        "testcases": testcases,
        "tests": total["tests"],
        "failures": total["failures"],
        "errors": total["errors"],
        "skipped": total["skipped"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", default="data/benchmark.json")
    parser.add_argument("--case-id", default=None)
    parser.add_argument("--repo", default=None, help="覆盖 benchmark 里的 repo_path")
    parser.add_argument("--command", default=None, help="覆盖 benchmark 里的 test_command")
    parser.add_argument("--label", default="run", help="before/after 等标签")
    parser.add_argument("--out-dir", default="reports")
    args = parser.parse_args()

    case = get_case(args.benchmark, args.case_id)
    repo_path = Path(args.repo or case["repo_path"]).resolve()
    command = args.command or case.get("test_command")

    if not command:
        raise SystemExit("benchmark 中没有 test_command，且未通过 --command 指定")

    print(f"[verifier] case: {case.get('id')}")
    print(f"[verifier] repo: {repo_path}")
    print(f"[verifier] label: {args.label}")

    proc, elapsed = run_command(command, repo_path)
    surefire = parse_surefire(repo_path)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    out_file = out_dir / f"verification_{case.get('id', 'case')}_{args.label}_{timestamp}.json"

    report = {
        "case_id": case.get("id"),
        "label": args.label,
        "repo_path": str(repo_path),
        "command": command,
        "returncode": proc.returncode,
        "elapsed_sec": round(elapsed, 2),
        "stdout_tail": "\n".join(proc.stdout.splitlines()[-80:]) if proc.stdout else "",
        "stderr_tail": "\n".join(proc.stderr.splitlines()[-80:]) if proc.stderr else "",
        "surefire": surefire,
        "timestamp": timestamp,
    }

    out_file.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n[verifier] 报告已写入: {out_file}")

    if surefire.get("found"):
        print(
            f"[verifier] 测试汇总: "
            f"tests={surefire['tests']} "
            f"failures={surefire['failures']} "
            f"errors={surefire['errors']} "
            f"skipped={surefire['skipped']}"
        )
    else:
        print(f"[verifier] 未解析到 Surefire 报告: {surefire.get('reason')}")

    if proc.returncode == 0:
        print("[verifier] Maven 退出码为 0")
    else:
        print(f"[verifier] Maven 退出码非 0: {proc.returncode}")


if __name__ == "__main__":
    main()