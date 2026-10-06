"""게이트 뮤테이션 테스트 — 훅 계약 테스트가 정말 게이트를 지키고 있는지 잰다.

방법: 레포를 임시 디렉터리에 복사하고, 차단 술어 하나를 무력화한 변형(뮤턴트)을 만든 뒤
test_hooks 를 그 복사본에서 돌린다. 테스트가 실패하면 뮤턴트를 "죽였다"(= 그 술어는
테스트로 지켜진다). 통과하면 살아남은 것이고, 그 술어가 망가져도 아무도 모른다는 뜻이다.

뮤턴트는 술어 함수 첫 줄에 고정값 return 을 끼워 넣거나 상수를 바꾸는 방식이다(외부 의존 없음).
느리다(뮤턴트마다 전체 스위트) — 기본 discover 에서는 건너뛰고, HARNESS_MUTATION=1 일 때만 돈다.

실행:  HARNESS_MUTATION=1 python3 -m unittest tests.test_mutation -v
"""
from __future__ import annotations
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (뮤턴트 id, 파일, 함수명 또는 None, 끼워 넣을 return 값 또는 (old, new) 치환)
MUTANTS = [
    ("pkgRef-never", "gate-engine.py", "p_pkg_ref", "None"),
    ("bodySizeOver-never", "gate-engine.py", "p_body_size_over", "False"),
    ("globExists-always", "gate-engine.py", "p_glob_exists", "True"),
    ("pathGlob-never", "gate-engine.py", "p_path_glob", "False"),
    ("escapeHatch-never", "gate-engine.py", "p_escape_hatch", "False"),
    ("protected-file-never", "gate-engine.py", "_protected_file", "False"),
    ("settings-off-never", "gate-engine.py", "settings_turns_off", "False"),
    ("shell-protected-never", "gate-engine.py", "shell_touches_protected", "None"),
    ("destructive-check-never", "block-destructive-git.py", "check", "None"),
    ("destructive-raw-never", "block-destructive-git.py", "raw_risky", "False"),
    ("destructive-root-never", "block-destructive-git.py", "dangerous_root", "False"),
    ("destructive-embedded-none", "block-destructive-git.py", "embedded_scripts", "[]"),
    ("delegation-zone-never", "block-impl-delegation.py", "in_code_zone", "''"),
    ("output-file-always", "require-agent-output-file.py", "output_targets", "['out.md']"),
    ("report-failures-shapes-none", "report-failures-gate.py", "cmd_shapes", "[]"),
    ("report-failures-blocks-none", "report-failures-gate.py", "blocks", "[]"),
    ("report-failures-denial-never", "report-failures-gate.py", None,
     ("DENIAL = re.compile(", "DENIAL = re.compile(r'(?!)') or re.compile(")),
    ("shell-heredoc-data-kept", "shellparse.py", "strip_heredoc_bodies", "cmd"),
    ("shell-quotes-kept", "shellparse.py", "neutralize_quotes", "cmd"),
    ("shell-targets-none", "shellparse.py", "write_targets", "[]"),
    ("delegation-git-never", "block-impl-delegation.py", "git_tree_write", "False"),
    ("install-args-none", "gate-engine.py", "_install_args", "[]"),
    ("commit-boundary-bands-off", "commit-boundary-gate.py", None,
     ("BANDS = (20, 40)", "BANDS = (10**9, 10**9)")),
]


def mutate(src: str, func, change) -> str:
    if func is None:
        old, new = change
        assert old in src, old
        return src.replace(old, new, 1)
    lines = src.split("\n")
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)def " + re.escape(func) + r"\(", line)
        if not m:
            continue
        j = i
        while not lines[j].rstrip().endswith(":"):  # 여러 줄 시그니처
            j += 1
        lines.insert(j + 1, m.group(1) + "    return " + change + "  # MUTANT")
        return "\n".join(lines)
    raise AssertionError(f"def {func} not found")


@unittest.skipUnless(os.environ.get("HARNESS_MUTATION"), "HARNESS_MUTATION=1 일 때만 (느림)")
class MutationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = Path(tempfile.mkdtemp(prefix="harness-mut-"))
        for d in ("hooks", "gates", "docs", "skills", "agents", "tests", "scripts", ".claude-plugin"):
            if (ROOT / d).exists():
                shutil.copytree(ROOT / d, cls.base / d, ignore=shutil.ignore_patterns("__pycache__"))
        cls.survivors = []

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)
        if cls.survivors:
            print("\n살아남은 뮤턴트:", ", ".join(cls.survivors), file=sys.stderr)

    def run_suite(self) -> int:
        e = {k: v for k, v in os.environ.items() if k != "HARNESS_MUTATION"}
        p = subprocess.run([sys.executable, "-m", "unittest", "tests.test_hooks"],
                           cwd=self.base, capture_output=True, text=True, env=e, timeout=600)
        return p.returncode

    def test_baseline_copy_passes(self):
        self.assertEqual(self.run_suite(), 0, "복사본 기준선이 먼저 통과해야 뮤테이션 결과가 의미 있다")

    def test_every_mutant_is_killed(self):
        for mid, fname, func, change in MUTANTS:
            with self.subTest(mutant=mid):
                path = self.base / "hooks" / fname
                original = path.read_text()
                try:
                    path.write_text(mutate(original, func, change))
                    killed = self.run_suite() != 0
                finally:
                    path.write_text(original)
                if not killed:
                    self.survivors.append(mid)
                self.assertTrue(killed, f"뮤턴트 {mid} 가 살아남았다 — {fname}:{func or change[0]} 를 지키는 테스트가 없다")


if __name__ == "__main__":
    unittest.main()
