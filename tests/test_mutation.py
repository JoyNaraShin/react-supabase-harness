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
    ("scope-applies-always", "scope.py", "applies", "True"),
    ("scope-kind-never-harness", "scope.py", "kind", "'other'"),
    ("scope-root-none", "scope.py", "project_root", "None"),
    ("bash-writes-none", "gate-engine.py", "_bash_writes", "[]"),
    ("spec-names-plain", "gate-engine.py", "_spec_names", "[spec]"),
    ("introduced-deps-never", "gate-engine.py", "_introduced_deps", "None"),
    ("post-check-noop", "gate-engine.py", "post_check", "None"),
    ("verdict-never", "gate-engine.py", "p_verdict", "(False, '')"),
    ("verdict-always", "gate-engine.py", "p_verdict", "(True, 'x')"),
    ("command-regex-never", "gate-engine.py", "p_command_regex", "(False, '')"),
    ("command-regex-always", "gate-engine.py", "p_command_regex", "(True, 'x')"),
    ("snapshot-post-noop", "snapshot-guard.py", "post", "None"),
    ("snapshot-never", "gitsnap.py", "snapshot", "None"),
    ("snapshot-secret-never", "gitsnap.py", "is_secret_name", "False"),
    ("snapshot-exclude-none", "gitsnap.py", "excluded_untracked", "[]"),
    ("expand-command-noop", "shellparse.py", "expand_command", "cmd"),
    ("rm-candidates-literal", "block-destructive-git.py", "rm_candidates", "[t]"),
    ("track-cd-never", "block-destructive-git.py", "track_cd", "False"),
    ("brace-expand-literal", "shellparse.py", "brace_expand", "[t]"),
    ("brace-range-none", "shellparse.py", "_brace_range", "None"),
    ("join-continuations-noop", "shellparse.py", "join_continuations", "cmd"),
    ("decode-ansi-noop", "shellparse.py", "decode_ansi_quotes", "cmd"),
    ("collapse-params-noop", "shellparse.py", "collapse_params", "cmd"),
    ("expand-line-noop", "shellparse.py", "_expand_line", "line"),
    ("rm-wrapper-never", "../bin/rm", "why_dangerous", '""'),
    ("rm-wrapper-nonrecursive", "../bin/rm", "parse", "(False, args)"),
    ("rm-install-noop", "install-rm-guard.py", "main", "None"),
    ("rm-install-stale-kept", "install-rm-guard.py", "drop_stale", "None"),
    ("denial-anywhere-in-output", "report-failures-gate.py", "is_denial", "bool(DENIAL.search(str(content or '')))"),
    ("segment-count-zero", "report-failures-gate.py", "segment_count", "0"),
    # 0.41.0 S4 — 읽기 전용 두 층이 report-failures-gate 에서 shellparse 로 옮겨 왔다(스냅샷과 공용)
    ("read-only-ignores-args", "shellparse.py", "segment_read_only",
     "bool(seg.split()) and os.path.basename(seg.split()[0]) in READ_ONLY_HEADS | {'git', 'sort', 'uniq', 'tee', 'gh', 'date'}"),
    ("segment-special-ignored", "shellparse.py", None,
     ('SEGMENT_SPECIAL = re.compile(r"[^A-Za-z0-9_ \\t./:@%+,=-]")', 'SEGMENT_SPECIAL = re.compile(r"(?!)")')),
    ("redirect-not-a-write", "shellparse.py", None,
     ('SEGMENT_SPECIAL = re.compile(r"[^A-Za-z0-9_ \\t./:@%+,=-]")', 'SEGMENT_SPECIAL = re.compile(r"[^A-Za-z0-9_ \\t./:@%+,=<>-]")')),
    ("sort-attached-o-ignored", "shellparse.py", None,
     ("all(set(t[1:]) <= SORT_SAFE for t in shorts)", "True")),
    ("call-level-full", "shellparse.py", "call_level", "FULL"),
    ("safe-name-always", "shellparse.py", "_safe_name", "True"),
    ("call-group-ignored", "shellparse.py", None,
     ('if re.search(r"(?<!\\$)\\(", masked):', 'if False:')),
    ("heredoc-tag-name-only", "shellparse.py", "heredoc_tag", "re.sub(r'[^A-Za-z0-9_].*', '', m.group(2).lstrip(chr(92)+chr(39)+chr(34)))"),
    ("guard-heredoc-bodies-kept", "block-destructive-git.py", "split_heredocs", "(command, [])"),
    ("leading-assignments-none", "shellparse.py", "leading_assignments", "{}"),
    ("changed-paths-none", "gitsnap.py", "changed_paths", "[]"),
    ("snapshot-read-only-always", "snapshot-guard.py", "_read_only", "True"),
    ("verify-failed-never", "block-destructive-git.py", "verify_failed", "None"),
    ("verdict-unparsed", "subagent-audit.py", "parse_verdict", "None"),
    ("bodySizeOver-never", "gate-engine.py", "p_body_size_over", "False"),
    ("globExists-always", "gate-engine.py", "p_glob_exists", "True"),
    ("pathGlob-never", "gate-engine.py", "p_path_glob", "False"),
    ("escapeHatch-never", "gate-engine.py", "p_escape_hatch", "False"),
    ("protected-file-never", "gate-engine.py", "_protected_file", "False"),
    ("settings-off-never", "gate-engine.py", "settings_turns_off", "False"),
    ("shell-protected-never", "gate-engine.py", "shell_touches_protected", "None"),
    ("destructive-check-never", "block-destructive-git.py", "check", "None"),
    ("short-flag-never", "block-destructive-git.py", "has_short_flag", "False"),
    ("nul-cut-identity", "shellparse.py", "nul_cut", "cmd"),
    # 0.43.0 — git 규칙은 정적 가드와 실행 시점 래퍼(bin/git)가 함께 쓰는 gitpolicy 한 곳
    ("gitpolicy-check-never", "gitpolicy.py", "check", "None"),
    ("gitpolicy-short-never", "gitpolicy.py", "has_short", "False"),
    ("gitpolicy-no-subcommand", "gitpolicy.py", "split", "(args, None, [], {})"),
    ("gitpolicy-verify-never", "gitpolicy.py", "verify_failed", "None"),
    ("gitpolicy-expand-identity", "gitpolicy.py", "expand", "(args, None)"),
    ("gitpolicy-builtin-shadowed", "gitpolicy.py", None, ("if sub.lower() in BUILTIN:", "if False:")),
    ("gitpolicy-alias-plain-split", "gitpolicy.py", None, ("words = shlex.split(exp)", "words = exp.split()")),
    ("gitpolicy-shell-alias-allowed", "gitpolicy.py", None, ('if exp.startswith("!"):', "if False:")),
    ("gitpolicy-long-opt-exact", "gitpolicy.py", "is_opt", "a.split('=', 1)[0] == name"),
    ("gitpolicy-inline-config-none", "gitpolicy.py", "inline_config", "{}"),
    ("gitpolicy-clean-config-ignored", "gitpolicy.py", None, ("if req is None and config_lookup:", "if False:")),
    ("gitpolicy-push-config-ignored", "gitpolicy.py", None, ("        if config_lookup:\n", "        if False:\n")),
    ("destructive-raw-never", "block-destructive-git.py", "raw_risky", "False"),
    ("destructive-root-never", "rmpolicy.py", "dangerous_root", "False"),
    ("rmpolicy-why-never", "rmpolicy.py", "why", "None"),
    ("rmpolicy-gitdirs-none", "rmpolicy.py", "git_dirs", "set()"),
    ("rmpolicy-ident-none", "rmpolicy.py", "_ident", "None"),
    ("rmpolicy-runtime-never", "rmpolicy.py", "runtime", "None"),
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
    # 0.40.0 S3 — 런처·git 전역 옵션·cd 추적·source 판정이 shellparse 로 옮겨 왔다
    ("strip-launchers-noop", "shellparse.py", "strip_launchers", "toks"),
    ("git-sub-none", "shellparse.py", "git_sub", "(-1, [])"),
    ("shell-words-none", "shellparse.py", "words", "[]"),
    ("cd-untracked", "shellparse.py", "_cd", "None"),
    ("sources-never", "shellparse.py", "_sources", "False"),
    ("source-comments-kept", "shellparse.py", "_strip_comments", "text"),
    ("heredoc-marker-never", "shellparse.py", "_quote_state", "(tuple(q or ()), None)"),
    ("snapshot-lossy-never", "snapshot-guard.py", "lossy", "False"),
    ("install-args-none", "gate-engine.py", "_install_args", "[]"),
    # 0.44.0 — plan-first 의 보장 층은 실행 전후 대조(명령이 실제로 바꾼 파일)
    ("prestate-never", "gate-engine.py", "record_prestate", "None"),
    ("changed-none", "gate-engine.py", "_changed", "None"),
    ("change-size-small", "gate-engine.py", "_change_size", "(False, '')"),
    ("background-never", "gate-engine.py", "_backgrounded", "False"),
    ("quoted-lines-split", "gate-engine.py", "_join_quoted_lines", "cmd"),
    ("head-text-none", "gate-engine.py", "_head_text", "None"),
    ("pre-dir-unchecked", "gate-engine.py", None, ("            if os.path.islink(x) or", "            if False and os.path.islink(x) or")),
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
        for d in ("hooks", "bin", "gates", "docs", "skills", "agents", "tests", "scripts", ".claude-plugin"):
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
