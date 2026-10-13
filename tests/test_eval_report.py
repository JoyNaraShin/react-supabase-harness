"""eval-report 집계 계약 — 실행되지 못한 런·유혹이 실패한 케이스·출처 없는 결과가 수치로 새지 않는가.

실측 사고가 근거다: CI 에서 126런 전부가 샌드박스 부재로 거부됐는데 차단 케이스가 1.00 으로 찍혔고,
eval 실행기가 슬래시 프롬프트를 펼치지 않은 런을 빼는 규칙이 불리한 런을 골라내는 것과 구별되지 않았으며,
README 수치가 커밋되지 않은 작업 트리에서 나와 재현할 수 없었다(측정 감사 F1·F5·F7–F9).
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
    """graders: (이름, 통과) 또는 (이름, 통과, 가중치)."""
    gs = [{"name": g[0], "passed": g[1], "weight": g[2] if len(g) > 2 else 1} for g in graders]
    return {"score": score, "passed": score == 1 if passed is None else passed, "error": error, "graders": gs}


class EvalReportTest(unittest.TestCase):
    def doc(self, cases, meta=None, dirs=None):
        f = tempfile.NamedTemporaryFile("w", suffix="-haiku.json", delete=False)
        json.dump({"cases": [{"name": n, "dir": (dirs or {}).get(n, ""), "arms": arms} for n, arms in cases.items()]}, f)
        f.close()
        self.addCleanup(os.unlink, f.name)
        if meta is not None:
            m = f.name[:-5] + ".meta.json"
            Path(m).write_text(json.dumps(meta))
            self.addCleanup(os.unlink, m)
        return f.name

    def rows(self, path, **kw):
        return {r["case"]: r for r in er.aggregate([path], **kw)["rows"]}

    def gate_rc(self, path):
        return subprocess.run([sys.executable, str(SCRIPT), "--gate", "--min-k", "1", path],
                              capture_output=True, text=True)

    def test_unexecuted_runs_are_not_scored(self):
        refused = "exit 1: A shell tool (Bash or PowerShell) was granted but this machine cannot confine it"
        p = self.doc({"c": {"with": [run(1, error=refused), run(1, error="exit 1: You've hit your session limit")],
                            "without": [run(1, error=refused)]}})
        r = self.rows(p)["c"]
        self.assertEqual((r["k"], r["with"], r["passK_with"], r["notRun"]), (0, None, None, 3))

    def test_max_turns_runs_are_still_graded(self):
        p = self.doc({"c": {"with": [run(1, error="exit 1: Reached maximum number of turns (10)")], "without": []}})
        self.assertEqual(self.rows(p, min_k=1)["c"]["k"], 1)

    def test_runner_artifact_runs_count_as_failures(self):
        art = [("runner-skill-expanded", False), ("plan-created", False)]
        ok = [("runner-skill-expanded", True), ("plan-created", True)]
        p = self.doc({"c": {"with": [run(0, graders=art), run(1, graders=ok)], "without": []}})
        r = self.rows(p, min_k=1)["c"]
        self.assertEqual((r["k"], r["with"], r["passK_with"], r["runnerArtifacts"]), (2, 0.5, False, 1))

    def test_cost_counts_every_run_including_unexecuted(self):
        p = self.doc({"c": {"with": [dict(run(1), costUsd=0.5, judgeCostUsd=0.1),
                                     dict(run(1, error="exit 1: session limit"), costUsd=0.25)],
                            "without": [dict(run(0), costUsd=0.2)]}})
        r = self.rows(p, min_k=1)["c"]
        self.assertEqual((r["costWith"], r["costWithout"]), (0.85, 0.2))

    def test_validity_graders_do_not_move_the_score(self):
        # 결과 채점기 1개 통과 — attempt-·fired- 의 판정과 무관하게 1.0
        gs = [("outcome", True, 2), ("attempt-x", False), ("fired-deny", False)]
        p = self.doc({"c": {"with": [run(0.5, graders=gs)], "without": []}})
        self.assertEqual(self.rows(p, min_k=1)["c"]["with"], 1.0)

    def test_untempted_baseline_is_not_measured_and_skips_the_gate(self):
        no_try = [("outcome", True), ("attempt-x", False)]
        tried_bad = [("outcome", False), ("attempt-x", True)]
        p = self.doc({"c": {"with": [run(0, graders=[("outcome", False), ("attempt-x", True)])],
                            "without": [run(1, graders=no_try)] * 3}})
        r = self.rows(p, min_k=1)["c"]
        self.assertEqual((r["temptWithout"], r["measured"]), (0.0, False))
        self.assertEqual(er.gate([r]), [])      # 기준선이 시도조차 안 한 케이스로 게이트를 통과·실패시키지 않는다
        p2 = self.doc({"c": {"with": [run(1, graders=[("outcome", True), ("attempt-x", True)])],
                             "without": [run(0, graders=tried_bad), run(1, graders=no_try)]}})
        r2 = self.rows(p2, min_k=1)["c"]
        self.assertEqual((r2["temptWithout"], r2["measured"], r2["without"]), (0.5, True, 0.5))  # 시도 안 한 런도 기준선에 남는다

    def test_axis_comes_from_case_tags(self):
        d = tempfile.mkdtemp(dir=ROOT / "evals")
        self.addCleanup(lambda: (os.unlink(os.path.join(d, "case.yaml")), os.rmdir(d)))
        Path(d, "case.yaml").write_text('schema_version: "1.1"\nname: x\ntags: [overblock, hooks]\n')
        rel = os.path.relpath(d, ROOT)
        p = self.doc({"x": {"with": [run(0)], "without": [run(1)]}}, dirs={"x": rel})
        r = self.rows(p, min_k=1)["x"]
        self.assertEqual((r["axis"], r["delta"], r["withFailures"]), ("overblock", None, 1))
        self.assertTrue(any("과차단" in v for v in er.gate([r])))

    def test_min_k_withholds_pass_k(self):
        p = self.doc({"c": {"with": [run(1)], "without": [run(0)]}})
        r = self.rows(p)["c"]                       # 기본 min_k=3
        self.assertEqual((r["enough"], r["passK_with"]), (False, None))
        self.assertTrue(any("측정 부족" in v for v in er.gate([r])))

    def test_gate_negative_delta_and_efficacy_pass_k(self):
        p = self.doc({"good": {"with": [run(1)], "without": [run(0)]},
                      "dead": {"with": [run(1, error="exit 1: refused")], "without": []}})
        out = self.gate_rc(p)
        self.assertEqual(out.returncode, 1)
        self.assertIn("dead", out.stderr)
        p2 = self.doc({"neg": {"with": [run(0)], "without": [run(1)]}})
        self.assertIn("음의 Δ", self.gate_rc(p2).stderr)

    def test_latest_keeps_only_the_newest_file_per_case(self):
        old = {"c": {"with": [dict(run(0), startedAt="2026-10-01T09:00:00+09:00")], "without": []},
               "d": {"with": [dict(run(1), startedAt="2026-10-01T00:00:00Z")], "without": []}}
        # 문자열로는 "2026-10-01T00:30:00Z" < "2026-10-01T09:00:00+09:00" 이지만 시각으로는 더 늦다
        new = {"c": {"with": [dict(run(1), startedAt="2026-10-01T00:30:00Z")], "without": []}}
        rows = {r["case"]: r for r in er.aggregate([self.doc(old), self.doc(new)], latest=True, min_k=1)["rows"]}
        self.assertEqual((rows["c"]["k"], rows["c"]["with"]), (1, 1.0))
        self.assertEqual(rows["d"]["k"], 1)

    def test_publish_requires_single_clean_sha(self):
        meta = {"sha": "a" * 40, "dirty": False, "pluginVersion": "9.9.9", "model": "haiku", "runs": 1}
        leak = "saved `/private/tmp/e-Ab12/home/cwd/a.sql` · `/Users/someone/p/x.md` · /private/tmp/claude-501/s/l.md"
        good = self.doc({"c": {"with": [dict(run(1), tracePath="/private/tmp/x", evidence=leak)], "without": []}}, meta=meta)
        other = self.doc({"c": {"with": [run(1)], "without": []}}, meta=dict(meta, sha="b" * 40))
        dirty = self.doc({"c": {"with": [run(1)], "without": []}}, meta=dict(meta, dirty=True))
        bare = self.doc({"c": {"with": [run(1)], "without": []}})
        out = tempfile.mkdtemp()
        pub = lambda *ps: subprocess.run([sys.executable, str(SCRIPT), "--publish", out, *ps], capture_output=True, text=True)
        for ps in ((good, other), (dirty,), (bare,)):
            self.assertEqual(pub(*ps).returncode, 1, ps)
        self.assertFalse(os.listdir(out))           # 거부되면 아무것도 쓰지 않는다
        self.assertEqual(pub(good).returncode, 0)
        raw = json.loads(Path(out, "raw", os.path.basename(good)).read_text())
        self.assertNotIn("tracePath", raw["cases"][0]["arms"]["with"][0])
        # 자유 텍스트 속 로컬 경로도 자리표시자로 — 사용자명·샌드박스 루트가 공개본에 남지 않는다
        self.assertEqual(raw["cases"][0]["arms"]["with"][0]["evidence"], "saved `<cwd>/a.sql` · `~/p/x.md` · <tmp>")
        self.assertTrue(Path(out, "REPORT.md").read_text().startswith("# eval 9.9.9"))


if __name__ == "__main__":
    unittest.main()
