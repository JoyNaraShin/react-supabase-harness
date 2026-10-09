"""eval-report 집계 계약 — 실행되지 못한 런이 점수로 새지 않는가.

실측 사고 두 건이 근거다: CI 에서 126런 전부가 샌드박스 부재로 거부됐는데 차단 케이스가 1.00 으로 찍혔고,
eval 실행기가 슬래시 프롬프트를 펼치지 않은 런이 스킬 실패로 집계됐다.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "eval-report.py"
spec = importlib.util.spec_from_file_location("eval_report", SCRIPT)
er = importlib.util.module_from_spec(spec)
spec.loader.exec_module(er)


def run(score, passed=None, error=None, graders=()):
    return {"score": score, "passed": score == 1 if passed is None else passed, "error": error,
            "graders": [{"name": n, "passed": p} for n, p in graders]}


class EvalReportTest(unittest.TestCase):
    def doc(self, cases):
        f = tempfile.NamedTemporaryFile("w", suffix="-haiku.json", delete=False)
        json.dump({"cases": [{"name": n, "arms": arms} for n, arms in cases.items()]}, f)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def rows(self, path):
        return {r["case"]: r for r in er.aggregate([path])["rows"]}

    def test_unexecuted_runs_are_not_scored(self):
        refused = "exit 1: A shell tool (Bash or PowerShell) was granted but this machine cannot confine it"
        p = self.doc({"c": {"with": [run(1, error=refused), run(1, error="exit 1: You've hit your session limit")],
                            "without": [run(1, error=refused)]}})
        r = self.rows(p)["c"]
        self.assertEqual((r["k"], r["with"], r["passK_with"], r["failedRuns"]), (0, None, None, 3))

    def test_max_turns_runs_are_still_graded(self):
        p = self.doc({"c": {"with": [run(1, error="exit 1: Reached maximum number of turns (10)")], "without": []}})
        self.assertEqual(self.rows(p)["c"]["k"], 1)

    def test_runner_artifact_runs_are_excluded(self):
        art = [("runner-skill-expanded", False), ("plan-created", False)]
        ok = [("runner-skill-expanded", True), ("plan-created", True)]
        p = self.doc({"c": {"with": [run(0, graders=art), run(1, graders=ok)], "without": [run(0)]}})
        r = self.rows(p)["c"]
        self.assertEqual((r["k"], r["with"], r["passK_with"], r["failedRuns"]), (1, 1.0, True, 1))
        self.assertTrue(any("runner" in x for x in r["failReasons"]))

    def test_gate_fails_when_a_case_has_no_valid_run(self):
        p = self.doc({"good": {"with": [run(1)], "without": [run(0)]},
                      "dead": {"with": [run(1, error="exit 1: refused")], "without": []}})
        out = subprocess.run([sys.executable, str(SCRIPT), "--min-pass-k", "0.5", p], capture_output=True, text=True)
        self.assertEqual(out.returncode, 1)
        self.assertIn("dead", out.stderr)

    def test_gate_threshold(self):
        p = self.doc({"a": {"with": [run(1), run(1)], "without": []},
                      "b": {"with": [run(1), run(0)], "without": []}})
        rc = lambda t: subprocess.run([sys.executable, str(SCRIPT), "--min-pass-k", t, p],
                                      capture_output=True).returncode
        self.assertEqual((rc("0.5"), rc("0.6")), (0, 1))

    def test_latest_keeps_only_the_newest_file_per_case(self):
        old = {"c": {"with": [dict(run(0), startedAt="2026-10-01T00:00:00Z")], "without": []},
               "d": {"with": [dict(run(1), startedAt="2026-10-01T00:00:00Z")], "without": []}}
        new = {"c": {"with": [dict(run(1), startedAt="2026-10-05T00:00:00Z")], "without": []}}
        rows = {r["case"]: r for r in er.aggregate([self.doc(old), self.doc(new)], latest=True)["rows"]}
        self.assertEqual((rows["c"]["k"], rows["c"]["with"]), (1, 1.0))   # 옛 0점은 빠진다
        self.assertEqual(rows["d"]["k"], 1)                                 # 새 파일에 없는 케이스는 옛 결과 유지


if __name__ == "__main__":
    unittest.main()
