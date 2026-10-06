"""scripts/synthesize.py 회계 계약 테스트.

종합 문서가 원장 행을 누락·강등·미처리로 넘기면 `--check` 는 반드시 exit 1 이어야 한다.
"""
from __future__ import annotations
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "synthesize.py"

REPORT = """# 리뷰

본문.

## 결함 원장

| id | severity | 축 | 위치 | 한 줄 |
|---|---|---|---|---|
| F1 | High | 보안 | a.ts:1 | 첫 결함 |
| F10 | Low | 가독성 | b.ts:2 | 열 번째 결함 |
"""

HEADER = "| 원행(agent:id) | sev(원본) | 위치 | 한 줄 | 목적지 | 처리 |\n|---|---|---|---|---|---|\n"


class SynthesizeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.report = self.d / "rev.md"
        self.report.write_text(REPORT)

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, timeout=20)

    def check(self, joined: str, *extra_reports: Path):
        j = self.d / "joined.md"
        j.write_text(joined)
        return self.run_script("--check", str(j), str(self.report), *map(str, extra_reports))

    def test_filled_join_passes(self):
        p = self.check(HEADER
                       + "| rev:F1 | High | a.ts:1 | 첫 결함 | W1 | fix |\n"
                       + "| rev:F10 | Low | b.ts:2 | 열 번째 | W2 | rejected — 재현 안 됨 |\n")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_unfilled_skeleton_fails(self):
        a = self.run_script(str(self.report))
        self.assertEqual(a.returncode, 0, a.stderr)
        p = self.check(a.stdout)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("처리 칸", p.stdout)

    def test_dropped_id_is_not_hidden_by_longer_id(self):
        # F1 과 F10 을 같은 등급으로 둬서 severity 불일치로 우연히 잡히는 경로를 막는다.
        self.report.write_text(REPORT.replace("| F10 | Low |", "| F10 | High |"))
        p = self.check(HEADER + "| rev:F10 | High | b.ts:2 | 열 번째 | W2 | fix |\n")
        self.assertEqual(p.returncode, 1)
        self.assertIn("누락(침묵 드롭): 1", p.stdout)

    def test_narrated_downgrade_fails(self):
        p = self.check(HEADER
                       + "| rev:F1 | Medium | a.ts:1 | 원본 High 였으나 낮춤 | W1 | fix |\n"
                       + "| rev:F10 | Low | b.ts:2 | 열 번째 | W2 | fix |\n")
        self.assertEqual(p.returncode, 1)
        self.assertIn("severity 불일치", p.stdout)

    def test_report_without_ledger_fails(self):
        bare = self.d / "silent.md"
        bare.write_text("# 리뷰\n\n문제를 몇 개 봤다.\n")
        p = self.check(HEADER
                       + "| rev:F1 | High | a.ts:1 | 첫 결함 | W1 | fix |\n"
                       + "| rev:F10 | Low | b.ts:2 | 열 번째 | W2 | fix |\n", bare)
        self.assertEqual(p.returncode, 1)
        self.assertIn("silent", p.stdout)

    def test_explicit_empty_ledger_is_valid(self):
        empty = self.d / "clean.md"
        empty.write_text("# 리뷰\n\n## 결함 원장\n\n결함 없음\n")
        p = self.check(HEADER
                       + "| rev:F1 | High | a.ts:1 | 첫 결함 | W1 | fix |\n"
                       + "| rev:F10 | Low | b.ts:2 | 열 번째 | W2 | fix |\n", empty)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_downgrade_inside_severity_cell_fails(self):
        p = self.check(HEADER
                       + "| rev:F1 | High → Low | a.ts:1 | 첫 결함 | W1 | fix |\n"
                       + "| rev:F10 | Low | b.ts:2 | 열 번째 | W2 | fix |\n")
        self.assertEqual(p.returncode, 1)

    def test_downgrade_in_disposition_cell_fails(self):
        p = self.check(HEADER
                       + "| rev:F1 | High | a.ts:1 | 첫 결함 | W1 | fix (Low 로 강등) |\n"
                       + "| rev:F10 | Low | b.ts:2 | 열 번째 | W2 | fix |\n")
        self.assertEqual(p.returncode, 1)

    def test_appendix_row_cannot_mask_downgraded_row(self):
        p = self.check(HEADER
                       + "| rev:F1 | Medium | a.ts:1 | 첫 결함 | W1 | fix |\n"
                       + "| rev:F10 | Low | b.ts:2 | 열 번째 | W2 | fix |\n\n## 부록\n\n"
                       + "| rev:F1 | High | a.ts:1 | 원본 | - | 참고 |\n")
        self.assertEqual(p.returncode, 1)

    def test_placeholder_disposition_counts_as_unfilled(self):
        p = self.check(HEADER
                       + "| rev:F1 | High | a.ts:1 | 첫 결함 | W1 | TBD |\n"
                       + "| rev:F10 | Low | b.ts:2 | 열 번째 | W2 | - |\n")
        self.assertEqual(p.returncode, 1)
        self.assertIn("처리 칸", p.stdout)

    def test_rows_under_ledger_subheading_are_counted(self):
        self.report.write_text(REPORT + "\n### 추가\n\n| F2 | Critical | 보안 | c.ts:3 | 소제목 아래 결함 |\n")
        p = self.check(HEADER
                       + "| rev:F1 | High | a.ts:1 | 첫 결함 | W1 | fix |\n"
                       + "| rev:F10 | Low | b.ts:2 | 열 번째 | W2 | fix |\n")
        self.assertEqual(p.returncode, 1)
        self.assertIn("`rev:F2`", p.stdout)

    def test_bare_word_none_is_not_an_empty_ledger(self):
        silent = self.d / "silent.md"
        silent.write_text("# 리뷰\n\n## 결함 원장\n\n특이사항 없음이라 보기 어렵다. F3 Critical 인증 우회\n")
        p = self.check(HEADER
                       + "| rev:F1 | High | a.ts:1 | 첫 결함 | W1 | fix |\n"
                       + "| rev:F10 | Low | b.ts:2 | 열 번째 | W2 | fix |\n", silent)
        self.assertEqual(p.returncode, 1)

    def test_last_ledger_heading_wins(self):
        self.report.write_text("# 리뷰\n\n## Ledger format notes\n\n형식 설명.\n\n" + REPORT.split("# 리뷰", 1)[1])
        p = self.check(HEADER
                       + "| rev:F1 | High | a.ts:1 | 첫 결함 | W1 | fix |\n"
                       + "| rev:F10 | Low | b.ts:2 | 열 번째 | W2 | fix |\n")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
