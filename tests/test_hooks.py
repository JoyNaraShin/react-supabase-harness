"""훅 계약 테스트 — 각 훅을 Claude Code 가 부르는 그대로(stdin JSON → stdout JSON) 실행한다.

왜 블랙박스인가: 이 하네스의 주장은 "게이트는 결정론적이다"이다. 내부 함수가 아니라
**훅 계약**(무엇을 넣으면 deny/block/알림/침묵 중 무엇이 나오나)을 고정해야 그 주장이 검증된다.

실행:  python3 -m unittest discover -s tests -v      (의존성 없음 — 표준 라이브러리만)
"""
from __future__ import annotations
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "hooks"

# import 구문 픽스처. 한 덩어리로 적으면 설치된 구버전 게이트가 이 파일 자체를 막으므로 패키지명 앞에서 끊는다.
IMPORT_MUI = "import { Button } from " + '"' + "@mui/material" + '";'
ADD_UI_LIB = "pnpm add " + "an" + "td"

# 관할 픽스처 — T1 훅(hooks/scope.py)은 하네스 관할 프로젝트에서만 돈다. 프로젝트를 지정하지 않은 호출은
# 이 디렉터리를 세션 프로젝트로 쓴다. 비관할에서 침묵하는지는 ScopeTest 가 같은 입력의 쌍으로 본다.
HARNESS_FIXTURE = tempfile.mkdtemp(prefix="harness-fixture-")
Path(HARNESS_FIXTURE, ".harness.json").write_text('{"role": "project"}')


def mark_harness(d) -> Path:
    """디렉터리를 하네스 관할 프로젝트로 표시한다(탑승 마커)."""
    Path(d, ".harness.json").write_text('{"role": "project"}')
    return Path(d)


def run_hook(name: str, payload, env: dict | None = None, cwd: str | None = None) -> dict | None:
    """훅을 실행하고 stdout JSON 을 돌려준다. 출력이 없으면 None(= 통과·침묵)."""
    e = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "HARNESS_GATE_"))}
    e["CLAUDE_PLUGIN_ROOT"] = str(ROOT)
    e.update(env or {})
    if "CLAUDE_PROJECT_DIR" not in e and not (isinstance(payload, dict) and payload.get("cwd")):
        e["CLAUDE_PROJECT_DIR"] = HARNESS_FIXTURE
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    cmd = ["bash" if name.endswith(".sh") else sys.executable, str(HOOKS / name)]
    p = subprocess.run(cmd, input=raw, capture_output=True, text=True, env=e, cwd=cwd, timeout=20)
    assert p.returncode == 0, f"{name} exit {p.returncode}: {p.stderr}"
    out = p.stdout.strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"_text": out}


def denied(out) -> bool:
    return bool(out) and out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def reason(out) -> str:
    return out["hookSpecificOutput"]["permissionDecisionReason"]


def context(out) -> str:
    return out["hookSpecificOutput"]["additionalContext"]


def blocked(out) -> bool:
    return bool(out) and out.get("decision") == "block"


def load_module(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HOOKS / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ─────────────────────────────────────────────────────────────────────────────
# gate-engine — 데이터로 정의된 룰을 결정론적 술어로 집행
# ─────────────────────────────────────────────────────────────────────────────
class GateEngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self.tmp.name)
        # 하네스 관할 프로젝트의 최소 조건: package.json + supabase/
        (self.proj / "package.json").write_text("{}")
        (self.proj / "supabase").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def gate(self, payload, **env):
        return run_hook("gate-engine.py", payload, env={"CLAUDE_PROJECT_DIR": str(self.proj), **env})

    def bash(self, command):
        return self.gate({"tool_name": "Bash", "tool_input": {"command": command}})

    def with_plan(self):
        (self.proj / "docs" / "plans").mkdir(parents=True)
        (self.proj / "docs" / "plans" / "phase-1.md").write_text("# plan")

    # no-ui-library — 설치 명령 · import · package.json 세 경로
    def test_ui_library_install_is_denied(self):
        self.assertTrue(denied(self.bash("pnpm add @radix-ui/react-dialog")))
        self.assertTrue(denied(self.bash("npm install antd@5")))

    def test_deny_message_names_the_package(self):
        # 회귀: 부정 조건(꺼진 탈출구)의 이름이 {match} 에 찍히던 버그
        out = self.bash("pnpm add antd")
        self.assertIn("`antd`", reason(out))
        self.assertNotIn("`ui-lib-gate-off` 도입", reason(out))

    def test_unrelated_install_passes(self):
        self.assertIsNone(self.bash("pnpm add zod"))
        self.assertIsNone(self.bash("git status"))

    def test_ui_library_import_is_denied(self):
        self.with_plan()
        out = self.gate({"tool_name": "Edit", "tool_input": {
            "file_path": str(self.proj / "src" / "App.tsx"), "new_string": IMPORT_MUI}})
        self.assertTrue(denied(out))

    def test_import_text_in_non_js_file_passes(self):
        # 회귀: .py·.md 안의 예시 문자열을 도입으로 오판하던 버그
        self.with_plan()
        for f in ["tests/test_x.py", "docs/guide.md"]:
            with self.subTest(f=f):
                out = self.gate({"tool_name": "Write", "tool_input": {
                    "file_path": str(self.proj / f), "content": IMPORT_MUI}})
                self.assertIsNone(out)

    def test_ui_library_in_package_json_is_denied(self):
        out = self.gate({"tool_name": "Edit", "tool_input": {
            "file_path": str(self.proj / "package.json"),
            "new_string": '"@chakra-ui/react": "^2.0.0"'}})
        self.assertTrue(denied(out))

    def test_escape_hatch_file_and_env(self):
        (self.proj / ".claude" / "state").mkdir(parents=True)
        (self.proj / ".claude" / "state" / "ui-lib-gate-off").touch()
        self.assertIsNone(self.bash("pnpm add antd"))
        (self.proj / ".claude" / "state" / "ui-lib-gate-off").unlink()
        self.assertIsNone(self.gate({"tool_name": "Bash", "tool_input": {"command": "pnpm add antd"}},
                                    HARNESS_GATE_UI_LIB_GATE_OFF="off"))

    # plan-first — 플랜 0개인 관할 프로젝트에서 큰 코드 작성만 막는다
    def test_new_source_file_without_plan_is_denied(self):
        out = self.gate({"tool_name": "Write", "tool_input": {
            "file_path": str(self.proj / "src" / "App.tsx"), "content": "export {}"}})
        self.assertTrue(denied(out))
        self.assertIn("src/App.tsx", reason(out))

    def test_small_edit_without_plan_passes(self):
        f = self.proj / "src" / "App.tsx"
        f.parent.mkdir()
        f.write_text("x")
        out = self.gate({"tool_name": "Edit", "tool_input": {"file_path": str(f), "new_string": "const a = 1;"}})
        self.assertIsNone(out)

    def test_plan_present_passes(self):
        self.with_plan()
        out = self.gate({"tool_name": "Write", "tool_input": {
            "file_path": str(self.proj / "src" / "App.tsx"), "content": "export {}"}})
        self.assertIsNone(out)

    def test_plan_template_alone_is_not_a_plan(self):
        # 템플릿으로 시작한 프로젝트는 docs/plans/*.template.md 를 갖고 태어난다 — 그걸 플랜으로 세면
        # 게이트가 첫날부터 죽는다. 세 훅(gate-engine·workflow-entry·boarding)의 플랜 정의가 같아야 한다.
        (self.proj / "docs" / "plans").mkdir(parents=True)
        (self.proj / "docs" / "plans" / "story.template.md").write_text("# template")
        out = self.gate({"tool_name": "Write", "tool_input": {
            "file_path": str(self.proj / "src" / "App.tsx"), "content": "export {}"}})
        self.assertTrue(denied(out))
        self.assertNotIn("잘게 쪼갠다", reason(out))

    def test_non_target_project_passes(self):
        (self.proj / "supabase").rmdir()  # 하네스 구조도 Supabase 도 없는 단발 프로토타입
        out = self.gate({"tool_name": "Write", "tool_input": {
            "file_path": str(self.proj / "src" / "App.tsx"), "content": "export {}"}})
        self.assertIsNone(out)

    # 프로젝트 룰 파일 — 같은 id 덮어쓰기 · 모르는 술어는 fail-open · JSONC
    def project_rules(self, text):
        d = self.proj / ".claude" / "gates"
        d.mkdir(parents=True)
        (d / "rules.jsonc").write_text(text)

    def test_project_override_can_disable_rule(self):
        self.project_rules('{"rules": [{"id": "no-ui-library", "enabled": false}]}')
        self.assertIsNone(self.bash("pnpm add antd"))

    def test_unknown_predicate_fails_open(self):
        self.project_rules(
            '{"rules": [{"id": "x", "on": ["Bash"], "when": [{"noSuchPredicate": 1}], "action": "deny"}]}')
        self.assertIsNone(self.bash("echo hi"))

    def test_project_rule_with_body_regex_warns(self):
        self.project_rules("""{
          // 주석과 후행 쉼표를 견딘다
          "rules": [{"id": "no-console", "on": ["Edit"], "action": "warn",
                     "when": [{"bodyRegex": {"pattern": "console\\\\.log", "except": "eslint-disable"}}],
                     "message": "console.log: {match}",}]
        }""")
        out = self.gate({"tool_name": "Edit", "tool_input": {"file_path": "README.md", "new_string": "console.log(1)"}})
        self.assertIn("console.log", out["hookSpecificOutput"]["additionalContext"])

    def test_garbage_stdin_is_silent(self):
        self.assertIsNone(self.gate("not json"))

    # 자기 보호 — 막힌 에이전트가 탈출구·룰 파일을 스스로 바꾸지 못한다
    def test_agent_cannot_open_its_own_escape_hatch(self):
        for payload in [
            {"tool_name": "Bash", "tool_input": {"command": "touch .claude/state/ui-lib-gate-off"}},
            {"tool_name": "Bash", "tool_input": {"command": "echo '{}' > .claude/gates/rules.jsonc"}},
            {"tool_name": "Write", "tool_input": {"file_path": str(self.proj / ".claude/gates/rules.jsonc"),
                                                  "content": "{}"}},
            {"tool_name": "Edit", "tool_input": {"file_path": str(self.proj / ".claude/state/plan-gate-off"),
                                                 "new_string": ""}},
        ]:
            with self.subTest(p=payload):
                self.assertTrue(denied(self.gate(payload)))

    def test_reading_gate_files_and_other_state_passes(self):
        self.assertIsNone(self.bash("cat .claude/gates/rules.jsonc"))
        self.assertIsNone(self.bash("ls .claude/state"))
        self.assertIsNone(self.gate({"tool_name": "Write", "tool_input": {
            "file_path": str(self.proj / ".claude/state/edit-count.json"), "content": "{}"}}))

    def test_self_protection_boundary(self):
        # 독립 재검증: 디렉터리 탈출구 · settings env · 읽기 오탐 · 경로 정규화
        (self.proj / ".claude" / "state" / "ui-lib-gate-off").mkdir(parents=True)
        self.assertTrue(denied(self.bash("pnpm add antd")))  # 디렉터리는 탈출구가 아니다
        for payload in [
            {"tool_name": "Bash", "tool_input": {"command": "mkdir -p .claude/state/plan-gate-off"}},
            {"tool_name": "Bash", "tool_input": {"command": "git checkout -- .claude/gates/rules.jsonc"}},
            {"tool_name": "Write", "tool_input": {
                "file_path": str(self.proj / ".claude/../.claude/gates/rules.jsonc"), "content": "{}"}},
            {"tool_name": "Edit", "tool_input": {"file_path": str(self.proj / ".claude/settings.local.json"),
                                                 "new_string": '"HARNESS_GATE_UI_LIB_GATE_OFF": "off"'}},
        ]:
            with self.subTest(p=payload):
                self.assertTrue(denied(self.gate(payload)))
        for c in ["ls .claude/state/*gate-off 2>/dev/null", "cat .claude/gates/rules.jsonc 2>&1",
                  "git status > /tmp/s.txt"]:
            with self.subTest(c=c):
                self.assertIsNone(self.bash(c))

    def test_deny_message_does_not_tell_agent_to_touch_hatch(self):
        out = self.bash("pnpm add antd")
        self.assertNotIn("touch", reason(out))


class StripJsoncTest(unittest.TestCase):
    """실측 버그 회귀: 정규식으로 주석을 벗기면 문자열 안 glob 이 지워졌다."""

    def setUp(self):
        self.strip = load_module("gate-engine.py").strip_jsonc

    def test_comment_markers_inside_strings_survive(self):
        src = '{"a": ["*/src/*.ts", "*/src/*.tsx"], // 주석\n "b": "[\\\\w.]{2,}" /* 블록 */}'
        self.assertEqual(json.loads(self.strip(src)), {"a": ["*/src/*.ts", "*/src/*.tsx"], "b": "[\\w.]{2,}"})

    def test_trailing_commas_removed_outside_strings_only(self):
        self.assertEqual(json.loads(self.strip('{"a": [1, 2,], "b": ", ]",}')), {"a": [1, 2], "b": ", ]"})

    def test_shipped_rules_parse(self):
        rules = json.loads(self.strip((ROOT / "gates" / "rules.jsonc").read_text()))["rules"]
        self.assertEqual({r["id"] for r in rules},
                         {"no-ui-library", "plan-first", "fit-before-phase", "no-pr-with-critical"})


# ─────────────────────────────────────────────────────────────────────────────
# block-destructive-git — 파괴적 명령 차단, /commit 승인 경로만 커밋 허용
# ─────────────────────────────────────────────────────────────────────────────
class DestructiveGitTest(unittest.TestCase):
    def check(self, command):
        return run_hook("block-destructive-git.py", {"tool_name": "Bash", "tool_input": {"command": command}})

    def test_destructive_commands_denied(self):
        for c in ["git push --force", "git push origin :main", "git reset --hard HEAD~1",
                  "rm -fr /usr/local", "rm -rf ~/cache", "rm -r -f ../up", "git commit -m x", "git -C . commit -m x",
                  'bash -c "git reset --hard"', "ls && git clean -fd"]:
            with self.subTest(c=c):
                self.assertTrue(denied(self.check(c)))

    def test_safe_commands_pass(self):
        # 상대경로 rm -rf 는 의도적으로 허용한다(빌드 산출물 정리) — 막는 건 절대·홈·상위 경로뿐.
        for c in ["git status", "git log --oneline", 'echo "rm -rf /"', "git push origin feat/x", "rm -fr build"]:
            with self.subTest(c=c):
                self.assertIsNone(self.check(c))

    def test_approved_commit_bypasses_commit_only(self):
        self.assertIsNone(self.check("CLAUDE_COMMIT_APPROVED=1 git commit -m x"))
        self.assertTrue(denied(self.check("CLAUDE_COMMIT_APPROVED=1 git push --force")))

    def test_launcher_prefixes_and_alternate_forms_denied(self):
        # 접두어·절대경로·대체 플래그로 통과하던 12건 + 변형
        for c in ["env git commit -m x", "command git commit -m x", "/usr/bin/git commit -m x",
                  "sudo rm -rf /", "nohup git push --force", "time git reset --hard",
                  "git push --delete origin main", "git push -fu origin main", "git push --mirror",
                  "git checkout .", "xargs rm -rf /", "find / -delete",
                  "sudo -u root env FOO=1 git push -f", "timeout 5 git reset --hard",
                  "builtin command git reset --hard", "git stash clear", "rm -rf .",
                  "git switch --discard-changes main", "find ~ -exec rm {} +"]:
            with self.subTest(c=c):
                self.assertTrue(denied(self.check(c)))

    def test_routine_commands_still_pass(self):
        for c in ["git push --force-with-lease=master:abc origin master", "git push -u origin dev",
                  "rm -fr node_modules/.cache", "find . -name '*.pyc'", "find build -delete",
                  "git checkout main", "git checkout -b feat/x", "git stash list", "env | sort",
                  "time pnpm test"]:
            with self.subTest(c=c):
                self.assertIsNone(self.check(c))

    def test_heredoc_body_is_data_unless_a_shell_reads_it(self):
        # 산문 속 "(git push --force)" 를 명령으로 오판하던 오탐
        self.assertIsNone(self.check("cat > notes.md <<'EOF'\n규칙 (git push --force 금지)\nEOF"))
        self.assertIsNone(self.check(
            "CLAUDE_COMMIT_APPROVED=1 git commit -F - <<'EOF'\nfix: (git reset --hard) 차단\nEOF"))
        self.assertTrue(denied(self.check("bash <<EOF\ngit push --force origin main\nEOF")))
        self.assertTrue(denied(self.check("cat <<EOF | sh\ngit reset --hard\nEOF")))

    # 독립 재검증에서 나온 우회·오탐 회귀
    def test_every_line_of_a_multiline_command_is_checked(self):
        # 개행이 공백으로 먹혀 둘째 줄 이후가 미검사였다
        for c in ["ls\ngit reset --hard", "# it's a comment\ngit reset --hard",
                  "CLAUDE_COMMIT_APPROVED=1 git commit -m x\ngit push --force origin main",
                  "echo a \\\n && git clean -fd"]:
            with self.subTest(c=c):
                self.assertTrue(denied(self.check(c)))

    def test_standard_approved_commit_heredoc_passes(self):
        # `-m "$(cat <<'EOF' …)"` 본문은 cat 이 받는다(셸 아님)
        msg = "fix: don't allow git push --force here\n\nCo-Authored-By: x"
        self.assertIsNone(self.check(
            "CLAUDE_COMMIT_APPROVED=1 git commit -m \"$(cat <<'EOF'\n" + msg + "\nEOF\n)\""))

    def test_commands_inside_shell_compound_statements_are_checked(self):
        # do/then/{ 뒤 명령
        for c in ['git branch | while read b; do git branch -D "$b"; done',
                  "if true; then git reset --hard; fi", "{ git clean -fd; }"]:
            with self.subTest(c=c):
                self.assertTrue(denied(self.check(c)))

    def test_rm_boundary(self):
        # 깊은 경로 정리는 일상이다 / 변수만 있는 경로·-f 없는 -r (`.git` 은 RmPolicyTest — 세션 프로젝트 기준)
        for c in ["rm -rf /tmp/build-x", "rm -rf /Users/me/proj/dist", "rm -rf ~/.cache/tool/x"]:
            with self.subTest(c=c):
                self.assertIsNone(self.check(c))
        for c in ['rm -rf "$DIR"/', "rm -rf $PWD", "rm -r ~", "rm -rf ~/Projects",
                  "rm -rf /Users/me"]:
            with self.subTest(c=c):
                self.assertTrue(denied(self.check(c)))

    def test_rm_expands_variables_defined_in_the_same_command(self):
        # 2026-10-09 실사용: `S=<깊은 스크래치 경로> && rm -rf $S` 가 '빈 값일 수 있는 변수'로 막혔다
        for c in ["S=/private/tmp/claude/scratch/x && rm -rf $S", "S=/tmp/a/b; rm -rf ${S}/c",
                  "export S=/tmp/a/b && rm -rf $S/out"]:
            with self.subTest(c=c):
                self.assertIsNone(self.check(c))
        for c in ["S=$(pwd) && rm -rf $S", "S=. && rm -rf $S", "S=~ ; rm -rf $S", "rm -rf $UNDEFINED_X/",
                  "S=/tmp/a/b && S=/Users/me && rm -rf $S", "rm -rf $HOME"]:
            with self.subTest(c=c):
                self.assertTrue(denied(self.check(c)))

    def test_rm_variable_values_that_can_change_stay_unknown(self):
        # v0.35.1 보안 회귀(백그라운드 보안 리뷰, 2026-10-09): 첫 대입값으로 판정해 실제 값이 `/` 인 삭제를 통과시켰다.
        # 재대입·조건부·서브셸 대입은 값이 달라질 수 있다 — 모르는 값으로 남아 막혀야 한다.
        for c in ["S=/tmp/a/b; S+=/../../..; rm -rf $S",
                  "S=/tmp/a/b; for S in /; do rm -rf $S; done",
                  "S=/tmp/a/b | rm -rf $S/",
                  "S=/tmp/a/b & rm -rf $S/",
                  "false && S=/tmp/a/b; rm -rf $S/",
                  "S=/tmp/a/b || S=/; rm -rf $S",
                  "S=/tmp/a/b; read S <<< /; rm -rf $S",
                  "S=/tmp/a/b; unset S; rm -rf $S/",
                  "S=/tmp/a/b; eval 'S=/'; rm -rf $S",
                  "S=/tmp/a/b; if true; then S=/; fi; rm -rf $S"]:
            with self.subTest(c=c):
                self.assertTrue(denied(self.check(c)))
        self.assertIsNone(self.check("S=/tmp/a/b; T=$S/c && rm -rf $T"))

    def test_rm_gaps_found_by_the_shell_differential(self):
        # 실제 bash 대조(tests/test_shell_differential.py)가 찾은, 0.35.0 부터 있던 구멍들
        for c in ["rm -rf /tmp/a/../..",                      # `..` 미정규화
                  "X=$(echo hi); rm -rf $S",                   # `);` 가 한 토큰이라 뒤 명령이 앞 명령의 인자로 읽힘
                  "cd / && rm -rf build",                      # cd 미추적 — 실제 대상은 /build
                  "rm -rf $X/tmp",                             # 모르는 변수는 비어 있을 수 있다 → /tmp
                  "S=/tmp/a/b && T=x & rm -rf $S/",            # `&` 는 and-or 목록 전체를 서브셸로
                  "cd ~ && rm -rf $S/../work",
                  'cd "$(pwd)" && rm -rf dist']:               # 위치를 모르면 상대 경로 삭제를 판정할 수 없다
            with self.subTest(c=c):
                self.assertTrue(denied(self.check(c)))
        for c in ['rm -rf "${TMPDIR:-/tmp}"/x', "cd /tmp && rm -rf build-x", "cd apps/web && rm -rf dist"]:
            with self.subTest(c=c):
                self.assertIsNone(self.check(c))

    def test_rm_brace_expansion_and_subshell_cd(self):
        # 생성형 차분 퍼즈(seed 1·2)가 찾은 것 — 중괄호 확장은 대상 하나를 여러 경로로 만들고,
        # 서브셸·백그라운드의 cd 는 현재 셸 위치를 바꿀 수도 안 바꿀 수도 있다
        for c in ["rm -rf /tmp/x{,/../..}", "rm -rf ~/{a,}", "rm -rf /{tmp,usr}/../",
                  "S=/; rm -rf $S{,/..}", "( cd / ) ; rm -rf build", "{ cd /; } & rm -rf build",
                  # 0.35.2 커밋 보안 리뷰 — 확장 상한에서 잘라 뒤를 안 봤고(fail-open), 펼친 `#` 이 주석이 됐다
                  "rm -rf /tmp/{a{1..200},..}/usr", "C=#; rm -rf $C /", "C=#; rm -rf x $C ~",
                  "rm -rf /tmp/{/../}/..", "rm -rf /tmp/x/{1..9..4}/../../..",
                  # 0.35.3 리뷰 뒤 문자 단위 퍼즈 — bash 가 토큰을 나누기 전에 하는 변환을 파서가 몰랐다
                  "r\\\nm -rf /", "rm \\\n-rf /usr", "rm -rf /us\\\nr",          # `\⏎` 는 지워진다(공백 아님)
                  "rm -rf $'/'", "rm -rf $'\\x2f'", "rm -rf $'\\057usr'", 'rm -rf $"/"',  # ANSI-C 따옴표
                  "S=/tmp/a/b/c; rm -rf /$$S", "rm -rf ${Susr: \\x2f,}/",       # `$$` · 공백 든 `${…}`
                  "rm -rf /./usr", "rm -rf .//*", "rm -rf ~/./Projects"]:      # `.`·`//` 정규화
            with self.subTest(c=c):
                self.assertTrue(denied(self.check(c)))
        for c in ["rm -rf dist/{a,b}", "rm -rf /tmp/{x,y}-cache", "rm -rf build/{1..3}"]:
            with self.subTest(c=c):
                self.assertIsNone(self.check(c))

    def test_git_boundary(self):
        # 오탐 / 미탐
        for c in ["find . -name '*.pyc' -delete", "git restore --staged a.ts", "git revert --no-commit HEAD"]:
            with self.subTest(c=c):
                self.assertIsNone(self.check(c))
        for c in ["git checkout origin/main src/a.ts", "git revert HEAD", "git cherry-pick abc",
                  "git filter-branch --tree-filter x", "git reflog expire --all", "git update-ref -d refs/heads/x",
                  "find . -delete"]:
            with self.subTest(c=c):
                self.assertTrue(denied(self.check(c)))

    def test_deny_message_does_not_reveal_commit_token(self):
        self.assertNotIn("CLAUDE_COMMIT_APPROVED", reason(self.check("git commit -m x")))


# ─────────────────────────────────────────────────────────────────────────────
# 위임 게이트 — 구현 위임 차단 · 장기 에이전트 파일 우선 산출
# ─────────────────────────────────────────────────────────────────────────────
class DelegationTest(unittest.TestCase):
    def agent(self, hook, subagent_type, prompt):
        return run_hook(hook, {"tool_name": "Agent",
                               "tool_input": {"subagent_type": subagent_type, "prompt": prompt}})

    # block-impl-delegation — 스폰 문구가 아니라 서브에이전트의 실제 쓰기를 본다
    def sub(self, tool, ti, agent="general-purpose", proj=None, agent_id="a1"):
        payload = {"tool_name": tool, "tool_input": ti, "agent_type": agent, "cwd": str(proj)}
        mark_harness(proj)
        if agent_id:
            payload["agent_id"] = agent_id
        return run_hook("block-impl-delegation.py", payload, env={"CLAUDE_PROJECT_DIR": str(proj)})

    def test_subagent_code_zone_writes_are_denied(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            for tool, ti in [("Write", {"file_path": str(p / "src/features/cart/api.ts"), "content": "x"}),
                             ("Edit", {"file_path": str(p / "supabase/migrations/1_init.sql"), "new_string": "x"}),
                             ("Edit", {"file_path": "package.json", "new_string": "x"}),
                             ("Bash", {"command": "cat > src/App.tsx <<'EOF'\nexport {}\nEOF"}),
                             ("Bash", {"command": "sed -i '' 's/a/b/' src/lib/format.ts"}),
                             ("Bash", {"command": "cp /tmp/x.ts src/x.ts"}),
                             ("Bash", {"command": "git apply /tmp/p.patch"})]:
                with self.subTest(tool=tool, ti=json.dumps(ti)[:70]):
                    out = self.sub(tool, ti, proj=p)
                    self.assertTrue(denied(out))
                    self.assertIn("다음 행동", reason(out))

    def test_subagent_non_code_writes_pass(self):
        # 리서치 산출·plan 작성·읽기·브라우저 조작 같은 기계 실행 위임은 막지 않는다
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            for agent, tool, ti in [
                ("general-purpose", "Write", {"file_path": "/tmp/r/cart-ledger.md", "content": "x"}),
                ("react-supabase-harness:planner", "Write", {"file_path": str(p / "docs/plans/phase-1.md"),
                                                             "content": "x"}),
                ("general-purpose", "Bash", {"command": "cat src/a.ts > /tmp/copy.ts"}),
                ("general-purpose", "Bash", {"command": "rg TODO src > /tmp/todo.txt"}),
                ("general-purpose", "Bash", {"command": "git diff src/ > /tmp/d.patch"})]:
                with self.subTest(agent=agent, ti=json.dumps(ti)[:70]):
                    self.assertIsNone(self.sub(tool, ti, agent=agent, proj=p))

    def test_main_session_writes_are_not_judged(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            self.assertIsNone(self.sub("Write", {"file_path": str(p / "src/a.ts"), "content": "x"},
                                       proj=p, agent_id=None))

    def test_user_listed_writer_type_may_write_code(self):
        # 위임 예외는 사용자가 프로젝트 룰 파일에 적는다(그 파일은 gate-engine 이 에이전트 쓰기로부터 보호)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / ".claude" / "gates").mkdir(parents=True)
            (p / ".claude" / "gates" / "rules.jsonc").write_text(
                '{ // 사용자 예외\n "delegation": {"writers": ["worktree-builder"]}, }')
            ti = {"file_path": str(p / "src/a.ts"), "content": "x"}
            self.assertIsNone(self.sub("Write", ti, agent="my-plugin:worktree-builder", proj=p))
            self.assertTrue(denied(self.sub("Write", ti, agent="general-purpose", proj=p)))

    def test_symlinked_alias_into_code_zone_is_denied(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "src").mkdir()
            os.symlink(p / "src", p / "alias")
            self.assertTrue(denied(self.sub("Write", {"file_path": str(p / "alias/a.ts"), "content": "x"},
                                            proj=p)))

    def test_long_agent_without_output_file_denied(self):
        self.assertTrue(denied(self.agent("require-agent-output-file.py", "general-purpose", "시장 조사해줘")))
        self.assertIsNone(self.agent("require-agent-output-file.py", "general-purpose", "조사해서 /tmp/r.md 에 저장"))
        self.assertIsNone(self.agent("require-agent-output-file.py", "Explore", "찾아줘"))

    def test_harness_reviewers_without_write_tool_pass(self):
        # 쓰기 도구가 없는 리뷰어에게 파일 산출을 요구하면 /review-* 가 막힌다
        for t in ["react-supabase-harness:craft-reviewer", "react-supabase-harness:stability-reviewer",
                  "react-supabase-harness:structure-fitness-reviewer", "Plan"]:
            with self.subTest(t=t):
                self.assertIsNone(self.agent("require-agent-output-file.py", t, "src/App.tsx 리뷰"))

    def test_input_path_alone_is_not_an_output_target(self):
        # 읽을 문서 경로만 있는 프롬프트는 산출 지시가 아니다
        self.assertTrue(denied(self.agent("require-agent-output-file.py", "general-purpose",
                                          "docs/plans/a.md 를 읽고 요약")))
        self.assertIsNone(self.agent("require-agent-output-file.py", "general-purpose",
                                     "results -> C:\\work\\out.md"))


# ─────────────────────────────────────────────────────────────────────────────
# report-failures-gate — 실패 누락 보고 · 정정 없는 번복
# ─────────────────────────────────────────────────────────────────────────────
def user_text(t):
    return {"type": "user", "message": {"content": [{"type": "text", "text": t}]}}


def tool_use(i, command):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": i, "name": "Bash", "input": {"command": command}}]}}


def tool_result(i, error, content="out"):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": i, "is_error": error, "content": content}]}}


def say(t):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": t}]}}


class ReportFailuresTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def stop(self, lines, **extra):
        p = Path(self.tmp.name) / "t.jsonl"
        p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines))
        return run_hook("report-failures-gate.py", {"transcript_path": str(p), **extra})

    def test_unmentioned_failure_blocks(self):
        out = self.stop([user_text("배포해"), tool_use("a", "vercel deploy"),
                         tool_result("a", True, "permission denied"), say("배포를 마쳤습니다.")])
        self.assertTrue(blocked(out))

    def test_mentioned_failure_passes(self):
        out = self.stop([user_text("배포해"), tool_use("a", "vercel deploy"),
                         tool_result("a", True, "permission denied"), say("첫 시도는 권한 거부로 실패했습니다.")])
        self.assertIsNone(out)

    def test_stop_hook_active_passes(self):
        out = self.stop([user_text("x"), tool_use("a", "ls"), tool_result("a", True), say("완료")],
                        stop_hook_active=True)
        self.assertIsNone(out)

    def test_irreversible_reversal_without_correction_blocks(self):
        lines = [user_text("지워"), tool_use("a", "rm -rf cache"), tool_result("a", True, "denied"),
                 say("이 명령은 실행할 수 없습니다. 실패했습니다."),
                 user_text("다시 해봐"), tool_use("b", "rm -rf cache"), tool_result("b", False),
                 say("삭제했습니다.")]
        self.assertTrue(blocked(self.stop(lines)))
        lines[-1] = say("앞서 못 한다고 한 판단이 틀렸습니다. 삭제했습니다.")
        self.assertIsNone(self.stop(lines))

    def test_reversible_reversal_only_notifies(self):
        lines = [user_text("a"), tool_use("a", "vercel ls"), tool_result("a", True, "permission denied"),
                 say("실패했습니다."),
                 user_text("b"), tool_use("b", "vercel ls"), tool_result("b", False), say("목록입니다.")]
        out = self.stop(lines)
        self.assertFalse(blocked(out))
        # 모델이 읽어야 하는 피드백 — systemMessage(사용자 전용)가 아니라 additionalContext
        self.assertIn("vercel ls", context(out))
        self.assertNotIn("systemMessage", out)

    def test_soft_notice_does_not_skip_failure_gate(self):
        # 번복 알림이 이번 턴의 미보고 실패를 가리면 안 된다
        lines = [user_text("a"), tool_use("a", "vercel ls"), tool_result("a", True), say("실패했습니다."),
                 user_text("b"), tool_use("b", "vercel ls"), tool_result("b", False),
                 tool_use("c", "vercel deploy"), tool_result("c", True, "denied"), say("목록입니다.")]
        self.assertTrue(blocked(self.stop(lines)))

    def test_final_text_comes_from_last_assistant_message(self):
        # transcript 에 이번 턴 답변이 아직 없어도 stdin 의 최종 답변으로 판정한다
        lines = [user_text("배포해"), tool_use("a", "vercel deploy"), tool_result("a", True, "denied")]
        self.assertIsNone(self.stop(lines, last_assistant_message="배포가 권한 거부로 실패했습니다."))
        self.assertTrue(blocked(self.stop(lines, last_assistant_message="배포를 마쳤습니다.")))

    def test_git_subcommands_are_distinct_identities(self):
        # `git status` 실패 뒤 `git log` 성공은 번복이 아니다
        lines = [user_text("a"), tool_use("a", "git status"), tool_result("a", True), say("실패했습니다."),
                 user_text("b"), tool_use("b", "git log"), tool_result("b", False), say("로그입니다.")]
        self.assertIsNone(self.stop(lines))

    def test_irreversible_git_reversal_blocks(self):
        lines = [user_text("a"), tool_use("a", "git push origin main"), tool_result("a", True, "Permission to use Bash has been denied"),
                 say("push 가 거부돼 실패했습니다."),
                 user_text("b"), tool_use("b", "git push origin main"), tool_result("b", False), say("올렸습니다.")]
        self.assertTrue(blocked(self.stop(lines)))

    def test_situational_rejection_and_user_decline_are_not_denials(self):
        # 4차 리뷰 N12: non-fast-forward 거절 뒤 pull --rebase → 같은 push 성공, 사용자가 거절했다가 나중에 허락
        for content in ["! [rejected] main -> main (non-fast-forward)\nerror: failed to push some refs",
                        "The user doesn't want to proceed with this tool use. The tool use was rejected."]:
            with self.subTest(content=content[:30]):
                lines = [user_text("a"), tool_use("a", "git push origin feat/x"), tool_result("a", True, content),
                         say("push 가 실패했습니다."),
                         user_text("b"), tool_use("b", "git push origin feat/x"), tool_result("b", False),
                         say("푸시했습니다.")]
                self.assertFalse(blocked(self.stop(lines)))

    def test_equivalent_command_forms_share_an_identity(self):
        # 4차 리뷰 R28: 플래그 표기·경로 표기·런처·리다이렉트가 달라도 같은 명령이다
        mod = load_module("report-failures-gate.py")
        base = mod.cmd_shapes("Bash", {"command": "rm -rf build"})
        for c in ["rm -r -f build", "rm --recursive --force ./build/", "env rm -rf build",
                  "timeout 10 rm -rf build", "rm -rf build 2>/dev/null", "rm -rf build | cat"]:
            with self.subTest(c=c):
                self.assertEqual(mod.cmd_shapes("Bash", {"command": c})[0], base[0])
        self.assertEqual(mod.cmd_shapes("Bash", {"command": "git push -f origin main"}),
                         mod.cmd_shapes("Bash", {"command": "git push --force origin main"}))

    def test_shell_grammar_and_interpreter_bodies_are_not_identities(self):
        # 2026-10-09 실사용: `for … ; do …; done` 의 `done` 이 '실패→성공한 명령'으로 잡혀 거짓 정정을 요구했다
        mod = load_module("report-failures-gate.py")
        shapes = mod.cmd_shapes("Bash", {"command": 'for d in a b; do rm -rf "$d"; done; if true; then ls x; fi'})
        self.assertFalse([x for x in shapes if x.split()[0] in ("done", "for", "do", "fi", "then", "if")], shapes)
        self.assertIn("rm -fr $d", shapes)
        body = mod.cmd_shapes("Bash", {"command": "python3 - <<'EOF'\nimport json\nprint(json.dumps(1))\nEOF"})
        self.assertFalse([x for x in body if x.startswith(("import", "print"))], body)
        self.assertIn("rm -fr x", mod.cmd_shapes("Bash", {"command": "bash <<'EOF'\nrm -rf x\nEOF"}))
        # 2026-10-11 실사용(0.35.4): heredoc 종료 줄 `PYEOF` 와 `2>&1` 의 `1` 이 명령으로 세어졌다
        shapes = mod.cmd_shapes("Bash", {"command": "python3 - <<'PYEOF'\nprint(1)\nPYEOF\nls -d x 2>&1; ls y >&2 |& cat &> z"})
        self.assertFalse([x for x in shapes if x in ("pyeof", "1", "2") or x.startswith(">")], shapes)
        self.assertIn("ls y", shapes)

    def test_done_keyword_does_not_count_as_a_reversal(self):
        loop = 'for d in a b; do rm -rf "$d"; done'
        lines = [user_text("a"), tool_use("a", "for d in x; do git push origin $d; done"),
                 tool_result("a", True, "PreToolUse:Bash hook error: blocked"), say("푸시가 차단됐습니다."),
                 user_text("b"), tool_use("b", loop), tool_result("b", False), say("정리했습니다.")]
        self.assertIsNone(self.stop(lines))

    def test_ordinary_failure_then_other_target_is_not_a_reversal(self):
        # A38: 판단이 아니라 상황인 실패(없음·네트워크) 뒤의 다른 대상 성공에 거짓 정정을 요구했다
        for first, err, second in [("rm -rf dist", "No such file or directory", "rm -rf node_modules"),
                                   ("git push origin feat-a", "Could not resolve host", "git push origin feat-b"),
                                   ("rm -rf dist", "No such file or directory", "rm -rf dist")]:
            with self.subTest(first=first, second=second):
                lines = [user_text("a"), tool_use("a", first), tool_result("a", True, err), say("실패했습니다."),
                         user_text("b"), tool_use("b", second), tool_result("b", False), say("됐습니다.")]
                self.assertIsNone(self.stop(lines))

    def test_read_only_tail_of_a_blocked_chain_is_not_a_reversal(self):
        # 2026-10-11 실사용: 막힌 체인 뒤에 붙인 `| head`·`; git status` 가 '실패했던 명령'으로 잡혀 매 턴 거짓 정정을 요구했다
        blocked_chain = "rm -rf cache; git status --short | head -3"
        for later in ["git status --short", "head -3 x.log", "ls dist | head"]:
            with self.subTest(later=later):
                lines = [user_text("a"), tool_use("a", blocked_chain),
                         tool_result("a", True, "PreToolUse:Bash hook error: rm -r 차단"), say("삭제가 차단됐습니다."),
                         user_text("b"), tool_use("b", later), tool_result("b", False), say("확인했습니다.")]
                self.assertIsNone(self.stop(lines))
        # 체인의 원인 후보(읽기 전용이 아닌 명령)는 계속 추적한다
        lines = [user_text("a"), tool_use("a", blocked_chain),
                 tool_result("a", True, "PreToolUse:Bash hook error: rm -r 차단"), say("차단됐습니다."),
                 user_text("b"), tool_use("b", "rm -rf cache"), tool_result("b", False), say("삭제했습니다.")]
        self.assertTrue(blocked(self.stop(lines)))
        # 혼자 실패한 읽기 전용 명령은 신원이다(체인이 아니면 원인이 분명하다)
        lines = [user_text("a"), tool_use("a", "ls private"), tool_result("a", True, "permission denied"),
                 say("볼 수 없습니다."), user_text("b"), tool_use("b", "ls private"), tool_result("b", False),
                 say("봤습니다.")]
        out = self.stop(lines)
        self.assertIsNotNone(out)
        self.assertIn("ls private", context(out))

    def test_writing_commands_in_a_blocked_chain_stay_tracked(self):
        # 0.36.2 리뷰: 이름만 보는 읽기 전용 목록이 `git branch -D`·`tee`·`sort -o`·`git remote remove` 까지 신원에서 지웠다
        for cmd in ["git branch -D feat", "git remote remove origin", "tee notes.md", "sort -o out.txt in.txt",
                    "git config user.name x", "gh pr create",
                    # 독립 검증이 찾은 것: 잘린 모양에서 사라지는 `--output`, 출력 파일 인자, 파일 리다이렉트
                    "git diff --binary --cached --output=f", "sort --check --debug --output=f in",
                    "uniq in.txt out.txt", "grep x a.txt > notes.md",
                    # 2차 독립 검증: 붙여 쓴 값·긴 옵션 축약·`-` 입력·명령 치환·특수 리다이렉트
                    "sort -oout.txt in.txt", "sort --outp=o2.txt in.txt", "uniq - out.txt",
                    "ls $(touch x)", "ls >| f.txt",
                    # 3차 독립 검증: 벗겨진 따옴표를 다시 쪼개면 사라지던 리다이렉트·인자, stash 의 --output
                    "grep x '#' > out.txt", "uniq 'a\\' out.txt",
                    "git stash show --output=f.txt",
                    # 4차 독립 검증: 변수·중괄호·글롭 확장이 만든 옵션·인자
                    "sort ${X:--oout.txt} a.txt", "sort {-o,out.txt} a.txt", "uniq {a.txt,b.txt}",
                    "git log {--output=o.txt,}", "uniq *.txt"]:
            with self.subTest(cmd=cmd):
                chain = f"{cmd}; git status --short | head -3"
                lines = [user_text("a"), tool_use("a", chain),
                         tool_result("a", True, "PreToolUse:Bash hook error: 차단"), say("차단됐습니다."),
                         user_text("b"), tool_use("b", cmd), tool_result("b", False), say("했습니다.")]
                out = self.stop(lines)                   # 되돌릴 수 있는 번복은 차단이 아니라 알림이다 — 신원으로 남는지만 본다
                self.assertIsNotNone(out)
                self.assertIn(cmd.split()[0], context(out))
        # 이스케이프된 따옴표는 공용 파서가 체인을 잘못 나눠 신원 비교가 안 되므로 판정 함수로 직접 본다
        gate = load_module("report-failures-gate.py")
        self.assertEqual(gate.read_only_shapes("Bash", {"command": 'grep "a\\"b" x.txt > out.txt; git status'}), set())
        # 5차 독립 검증: 함수 재정의·zsh 프로세스 치환·`$''(` — 원문 허용 문자 밖이면 어느 하위 명령도 읽기 전용이 아니다
        for cmd in ["head() { touch b; }; head", "sort =(touch b)", "ls $''(touch x); git status", "ls *(e:touch x:)",
                    # 6차: 빈 따옴표 인자, 같은 호출 안의 재바인딩, 셸이 공백으로 보지 않는 문자 뒤의 리다이렉트
                    "git config user.name ''; git status", "PATH=/tmp/x; head a", "hash -p /bin/rm head; head v",
                    "head in >\v/dev/null; ls"]:
            with self.subTest(direct=cmd):
                self.assertEqual(gate.read_only_shapes("Bash", {"command": cmd}), set())
        # 같은 하위 명령의 조회 형태는 여전히 읽기 전용이다
        for later in ["git branch --show-current", "git remote -v", "gh pr view 3", "gh pr view 3 --json title",
                      "git config user.name", "git log --oneline -5", "git status --short 2>/dev/null",
                      "sort -rn", "uniq -c", 'grep -n "a b" x.txt']:
            with self.subTest(later=later):
                chain = f"rm -rf cache; {later}"
                lines = [user_text("a"), tool_use("a", chain),
                         tool_result("a", True, "PreToolUse:Bash hook error: 차단"), say("차단됐습니다."),
                         user_text("b"), tool_use("b", later), tool_result("b", False), say("확인했습니다.")]
                self.assertIsNone(self.stop(lines))

    def test_denial_words_in_command_output_are_not_a_denial(self):
        # 실행 실패의 **출력**에 든 "차단"·"blocked" 는 막힘이 아니다(하네스 소스를 grep 하다 실패한 경우)
        # 읽기 전용이 아닌 단일 명령으로 잰다 — 읽기 전용 제외 규칙이 결과를 가리지 않게
        lines = [user_text("a"), tool_use("a", "npm test"),
                 tool_result("a", True, "Exit code 1\nFAIL gate: rm -r 차단 — expected blocked"), say("실패했습니다."),
                 user_text("b"), tool_use("b", "npm test"), tool_result("b", False), say("통과했습니다.")]
        self.assertIsNone(self.stop(lines))
        # 실행 시점 rm 가드의 거부는 실행 실패 형식이어도 막힘이다
        lines = [user_text("a"), tool_use("a", "rm -rf cache"),
                 tool_result("a", True, "Exit code 1\n[react-supabase-harness] rm -r 차단 — 실제 대상"),
                 say("막혔습니다."), user_text("b"), tool_use("b", "rm -rf cache"), tool_result("b", False),
                 say("삭제했습니다.")]
        self.assertTrue(blocked(self.stop(lines)))

    def test_denied_command_shapes_are_normalized(self):
        # A37: 같은 명령을 이어 붙이거나 플래그 순서만 바꿔도 번복은 번복이다
        for second in ["rm -fr cache", "cd . && rm -rf cache", "sudo rm -rf cache"]:
            with self.subTest(second=second):
                lines = [user_text("a"), tool_use("a", "rm -rf cache"), tool_result("a", True, "denied by hook"),
                         say("실패했습니다."),
                         user_text("b"), tool_use("b", second), tool_result("b", False), say("삭제했습니다.")]
                self.assertTrue(blocked(self.stop(lines)))

    def test_generic_interpreters_are_not_tracked(self):
        lines = [user_text("a"), tool_use("a", "python3 -"), tool_result("a", True), say("실패했습니다."),
                 user_text("b"), tool_use("b", "python3 -"), tool_result("b", False), say("됐습니다.")]
        self.assertIsNone(self.stop(lines))

    def test_unreadable_transcript_fails_open(self):
        self.assertIsNone(run_hook("report-failures-gate.py", {"transcript_path": "/nonexistent.jsonl"}))


# ─────────────────────────────────────────────────────────────────────────────
# commit-boundary-gate — 미커밋 변경이 띠를 넘으면 띠당 1회 알림
# ─────────────────────────────────────────────────────────────────────────────
class CommitBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / ".gitignore").write_text(".claude/\n.harness.json\n")
        mark_harness(self.repo)

    def tearDown(self):
        self.tmp.cleanup()

    def files(self, n):
        for i in range(n):
            (self.repo / f"f{i}.txt").write_text(str(i))

    def stop(self, **extra):
        # 상태는 저장소 밖(~/.claude/state)에 쓴다 — HOME 을 임시 디렉터리로 격리한다.
        return run_hook("commit-boundary-gate.py", {"cwd": str(self.repo), **extra},
                        env={"HOME": str(self.repo / ".home")})

    def test_below_band_is_silent(self):
        self.files(5)
        self.assertIsNone(self.stop())

    def test_band_notifies_once(self):
        self.files(25)
        out = self.stop()
        self.assertIn("미커밋 변경", context(out))
        self.assertFalse(blocked(out))  # 알림만 — 차단하지 않는다
        self.assertIsNone(self.stop())  # 같은 띠에서 반복 금지

    def test_new_directory_counts_each_file(self):
        # A41: 새 feature 폴더 하나(파일 25개)가 porcelain 에서 한 줄로 접혀 1개로 세였다
        d = self.repo / "src" / "features" / "cart"
        d.mkdir(parents=True)
        for i in range(25):
            (d / f"f{i}.ts").write_text(str(i))
        self.assertIn("25개", context(self.stop()))

    def test_incidental_staged_word_does_not_mark_band_handled(self):
        self.files(25)
        out = self.stop(last_assistant_message="I staged nothing yet.")
        self.assertIn("미커밋 변경", context(out))

    def test_state_file_stays_out_of_the_repo(self):
        self.files(25)
        self.stop()
        self.assertFalse((self.repo / ".claude" / "state" / "commit-boundary.json").exists())

    def test_band_drop_rearms_the_alert(self):
        # 40 띠에서 운 뒤 일부 커밋으로 20 띠로 내려갔다가 다시 40 을 넘으면 다시 운다
        self.files(45)
        self.stop()
        for i in range(20, 45):
            (self.repo / f"f{i}.txt").unlink()
        self.assertIsNone(self.stop())
        self.files(45)
        self.assertIn("40+", context(self.stop()))

    def test_proposal_in_last_assistant_message_silences(self):
        self.files(25)
        self.assertIsNone(self.stop(last_assistant_message="커밋 경계를 이렇게 나누자: ..."))

    def test_next_band_notifies_again(self):
        self.files(25)
        self.stop()
        self.files(45)
        self.assertIn("40+", context(self.stop()))

    def test_escape_hatch(self):
        (self.repo / ".claude" / "state").mkdir(parents=True)
        (self.repo / ".claude" / "state" / "commit-boundary-gate-off").touch()
        self.files(25)
        self.assertIsNone(self.stop())

    def test_not_a_repo_fails_open(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(run_hook("commit-boundary-gate.py", {"cwd": d}))


# ─────────────────────────────────────────────────────────────────────────────
# review-protocol — 리뷰 요청에만 프로토콜 주입
# ─────────────────────────────────────────────────────────────────────────────
class ReviewProtocolTest(unittest.TestCase):
    def prompt(self, text):
        return run_hook("review-protocol.sh", {"prompt": text})

    def test_review_request_injects_protocol(self):
        for t in ["이 코드 리뷰해줘", "보안 감사 부탁", "review this PR"]:
            with self.subTest(t=t):
                out = self.prompt(t)
                self.assertIn(f"{ROOT}/docs/REVIEW-PROTOCOL.md", out["_text"])

    def test_non_review_prompts_are_silent(self):
        for t in ["감사합니다", "리뷰한거야?", "some-review 스킬 설명", "the review table needs a rating column"]:
            with self.subTest(t=t):
                self.assertIsNone(self.prompt(t))

    # 실측 코퍼스 — 리뷰 요청 8건 미탐, 비리뷰 요청 5건 오탐이었다
    def test_audit_corpus_review_requests_fire(self):
        for t in ["이 PR 검토해줘", "src/features/auth 점검 부탁해", "이 코드 문제점 찾아줘",
                  "평가해줘 이 설계", "보안 점검 해줘", "can you review src/lib/format.ts?",
                  "please review PR #12", "Critique my schema"]:
            with self.subTest(t=t):
                self.assertIsNotNone(self.prompt(t))

    def test_audit_corpus_review_as_entity_or_followup_is_silent(self):
        # "리뷰"는 커머스 도메인의 1급 엔티티 — 리뷰 기능을 만드는 요청에 위임 지시를 주입하면
        # 구현 비위임(RULES §7)과 정면 충돌한다.
        for t in ["상품 리뷰 작성 기능을 만들고 싶어", "리뷰 테이블에 rating 컬럼 추가해줘",
                  "리뷰 목록 화면 진행하자", "코드 리뷰 코멘트 반영해줘", "/review-craft 결과 보고 수정 진행해"]:
            with self.subTest(t=t):
                self.assertIsNone(self.prompt(t))


# ─────────────────────────────────────────────────────────────────────────────
# 매니페스트 — hooks.json 이 가리키는 파일이 실재하고 버전이 일치한다
# ─────────────────────────────────────────────────────────────────────────────
class StackComplianceTest(unittest.TestCase):
    """선언(deps·alias)과 사용(우회 코드) 두 층. 템플릿 프로젝트는 선언을 다 갖춰 ①이 침묵한다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proj = mark_harness(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def full_stack(self):
        (self.proj / "package.json").write_text(json.dumps({"dependencies": {
            "react": "19", "@tanstack/react-query": "5"}, "devDependencies": {"tailwindcss": "4"}}))
        (self.proj / "tsconfig.json").write_text('{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}')

    def write(self, name, content):
        return run_hook("stack-compliance-guard.py",
                        {"tool_name": "Write", "tool_input": {
                            "file_path": str(self.proj / "src" / name), "content": content}},
                        env={"CLAUDE_PROJECT_DIR": str(self.proj)})

    def test_missing_declarations_are_reported_once(self):
        (self.proj / "package.json").write_text('{"dependencies": {"react": "19"}}')
        self.assertIn("Tailwind", context(self.write("App.tsx", "export {}")))
        self.assertIsNone(self.write("B.tsx", "export {}"))

    def test_clean_code_in_full_stack_project_is_silent(self):
        self.full_stack()
        self.assertIsNone(self.write("App.tsx", "import { x } from '@/lib/x';\nexport const A = () => null;"))

    def test_bypass_signals_fire_in_full_stack_project(self):
        self.full_stack()
        cases = [
            ("A.tsx", "import { db } from '../../lib/db';", "상대경로"),
            ("B.tsx", "import './B.css';\nexport {}", "CSS"),
            ("C.tsx", "useEffect(() => { fetch('/api/x').then(setX) }, []);", "TanStack"),
            ("D.tsx", "useEffect(() => { supabase.from('posts').select('*') }, []);", "TanStack"),
        ]
        state = self.proj / ".claude" / "state" / "stack-compliance.json"
        for name, code, needle in cases:
            with self.subTest(name=name):
                state.unlink(missing_ok=True)  # 신호당 1회 — 같은 신호의 다른 형태를 보려면 비운다
                self.assertIn(needle, context(self.write(name, code)))

    def test_each_bypass_signal_fires_once_per_project(self):
        self.full_stack()
        self.assertIsNotNone(self.write("A.tsx", "import { db } from '../../lib/db';"))
        self.assertIsNone(self.write("B.tsx", "import { db } from '../../lib/db';"))

    def test_non_react_project_is_silent(self):
        (self.proj / "package.json").write_text('{"dependencies": {"commander": "1"}}')
        self.assertIsNone(self.write("cli.ts", "import x from '../../x';"))


class SecondReverifyRegressionTest(unittest.TestCase):
    """2차 독립 재검증에서 재현된 우회·오탐. 재현 입력을 그대로 고정한다."""

    def bash_destructive(self, command):
        return run_hook("block-destructive-git.py", {"tool_name": "Bash", "tool_input": {"command": command}})

    def test_unparseable_command_still_blocks_destructive_patterns(self):
        # 파싱 실패 폴백이 부분문자열 목록이라 `git commit`·`push -f` 가 통과했다(fail-closed 주장 반증).
        # 승인 커밋 접두어 뒤에 붙인 명령도 폴백에서 검사해야 한다.
        # `$'it\'s'` 는 0.35.4 부터 bash 처럼 풀려 파싱된다 — 진짜로 닫히지 않은 따옴표를 쓴다
        unparseable = "echo 'unterminated"
        for c, want in [(f"{unparseable} && git commit -m x", True),
                        (f"{unparseable} && git push -f origin main", True),
                        ("CLAUDE_COMMIT_APPROVED=1 git commit -m \"a $'x\\'\" && git push --force", True),
                        ("CLAUDE_COMMIT_APPROVED=1 git commit -m \"a $'x\\'\" && rm -rf ~", True),
                        ("CLAUDE_COMMIT_APPROVED=1 git commit -m \"a $'x\\'\"", False),
                        (f"{unparseable} && git push --force-with-lease origin main", False),
                        (f"{unparseable} && ls", False)]:
            with self.subTest(c=c):
                self.assertEqual(denied(self.bash_destructive(c)), want)

    def test_embedded_and_piped_commands_are_scanned(self):
        for c, want in [("ls |& git push --force", True),
                        ("echo `git reset --hard`", True),
                        ("echo \"x `git reset --hard`\"", True),
                        ("bash <<< 'git reset --hard'", True),
                        ("echo 'git push --force' | sh", True),
                        ("find . -type f -delete", True),   # -type 은 범위를 좁히는 필터가 아니다
                        ("rm -rf $(pwd)", True),            # 따옴표 없는 $(pwd)
                        ("find . -name '*.tmp' -delete", False),
                        ("echo 'use `git reset --hard` carefully'", False),
                        ("echo `date`", False),
                        ("rm -rf build", False)]:
            with self.subTest(c=c):
                self.assertEqual(denied(self.bash_destructive(c)), want)

    def test_shell_self_protection_survives_path_splitting(self):
        with tempfile.TemporaryDirectory() as d:
            def gate(c):
                return run_hook("gate-engine.py", {"tool_name": "Bash", "tool_input": {"command": c}},
                                env={"CLAUDE_PROJECT_DIR": d})
            for c, want in [("cd .claude/state && touch plan-gate-off", True),
                            ("D=.claude/state; touch $D/plan-gate-off", True),
                            ("touch .claude/{state,x}/plan-gate-off", True),
                            ("touch .cl''aude/st\"ate\"/plan-gate-off", True),
                            ("cd .claude/gates && sed -i s/deny/warn/ rules.jsonc", True),
                            ("ls .claude/state 2>/dev/null", False),
                            ("cat .claude/gates/rules.jsonc", False),
                            ("pnpm build > build.log", False)]:
                with self.subTest(c=c):
                    self.assertEqual(denied(gate(c)), want)

    def test_settings_and_installed_rules_are_protected(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "package.json").write_text("{}")
            (Path(d) / "supabase").mkdir()
            s = str(Path(d) / ".claude" / "settings.json")

            def gate(payload, **env):
                return run_hook("gate-engine.py", payload, env={"CLAUDE_PROJECT_DIR": d, **env})

            def edit(path, body):
                return {"tool_name": "Edit", "tool_input": {"file_path": path, "new_string": body}}

            pkg = "shad" + "cn"
            for payload, want in [
                (edit(s, '{"disableAllHooks": true}'), True),
                (edit(s, '{"enabledPlugins": {"react-supabase-harness@react-supabase": false}}'), True),
                (edit(s, '{"env": {"HARNESS\\u005fGATE_PLAN_GATE_OFF": "1"}}'), True),
                (edit(s, '{"permissions": {"allow": ["Bash(ls)"]}}'), False),
                (edit(str(Path.home() / ".claude/plugins/cache/x/react-supabase-harness/0.31.0/gates/rules.jsonc"), "{}"), True),
                ({"tool_name": "Bash", "tool_input": {"command": "echo '{\"disableAllHooks\": true}' > .claude/settings.json"}}, True),
                ({"tool_name": "Bash", "tool_input": {"command": "pnpm dlx " + pkg + "@latest init"}}, True),
                ({"tool_name": "Bash", "tool_input": {"command": "bunx " + pkg + " add button"}}, True),
                ({"tool_name": "Bash", "tool_input": {"command": "pnpm add " + "radix" + "-ui"}}, True),
                ({"tool_name": "Bash", "tool_input": {"command": "pnpm dlx create-vite"}}, False),
            ]:
                with self.subTest(p=json.dumps(payload)[:90]):
                    self.assertEqual(denied(gate(payload)), want)
            rules_in_root = edit("/opt/plugroot/gates/rules.jsonc", "{}")
            self.assertTrue(denied(gate(rules_in_root, CLAUDE_PLUGIN_ROOT="/opt/plugroot")))

    def test_spawn_prompt_wording_is_no_longer_judged(self):
        # 문구 판정은 표현 변형에 놓치고 정상 리서치 지시를 막았다(3차 리뷰). 스폰 자체는 통과하고,
        # 판정은 서브에이전트가 실제로 코드 영역을 쓰려는 순간에 한다.
        for p in ["src/auth.ts 에 로그인 로직을 구현할 것",
                  "Audit src/ for security issues. Create a table of findings in /tmp/r/r.md."]:
            with self.subTest(p=p):
                self.assertIsNone(run_hook("block-impl-delegation.py", {"tool_name": "Agent", "tool_input": {
                    "prompt": p, "subagent_type": "general-purpose"}}))


class BoardingGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self.tmp.name)
        (self.proj / ".git").mkdir()
        (self.proj / "package.json").write_text("{}")
        (self.proj / "supabase").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def run_guard(self):
        return run_hook("harness-boarding-guard.py", {"cwd": str(self.proj)},
                        env={"CLAUDE_PROJECT_DIR": str(self.proj)})

    def mark(self):
        (self.proj / ".harness.json").write_text('{"harness": "react-supabase-harness", "role": "project"}')

    def plan(self, name="phase-1.md"):
        (self.proj / "docs" / "plans").mkdir(parents=True, exist_ok=True)
        (self.proj / "docs" / "plans" / name).write_text("# plan")

    def test_no_marker_no_plan_speaks(self):
        self.assertIn("미탑승", context(self.run_guard()))

    def test_marker_alone_does_not_silence(self):
        # 마커를 만드는 것만으로 조용해지면 /phase 건너뛰기를 못 본다
        self.mark()
        self.plan("story.template.md")
        self.assertIn("/phase 를 밟지 않았다", context(self.run_guard()))

    def test_marker_and_real_plan_is_silent(self):
        self.mark()
        self.plan()
        self.assertIsNone(self.run_guard())

    def test_placeholder_is_hard_even_with_marker_and_plan(self):
        self.mark()
        self.plan()
        (self.proj / "README.md").write_text("# __PROJECT_NAME__")
        self.assertIn("__PROJECT_NAME__", context(self.run_guard()))


class ManifestTest(unittest.TestCase):
    def test_every_wired_hook_exists(self):
        h = json.loads((HOOKS / "hooks.json").read_text())["hooks"]
        for event, groups in h.items():
            for g in groups:
                for hook in g["hooks"]:
                    f = hook["command"].split("/hooks/")[1].rstrip('"')
                    with self.subTest(event=event, f=f):
                        self.assertTrue((HOOKS / f).is_file())

    def test_versions_match(self):
        plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
        entry = next(p for p in market["plugins"] if p["name"] == plugin["name"])
        self.assertEqual(entry["version"], plugin["version"])

    def test_skills_and_agents_reference_plugin_files_through_plugin_root(self):
        # 설치된 프로젝트의 cwd 에는 하네스 docs/ 가 없다. 스킬·에이전트 본문은
        # `${CLAUDE_PLUGIN_ROOT}` 가 로드 시점에 치환되므로 그 경로로만 참조해야 한다.
        import re
        bare = re.compile(r"(?<![\w/.$}-])(docs/(RULES|WORKFLOW|PLANNING|INFRA|REVIEW-PROTOCOL|BOOTSTRAP)\.md"
                          r"|agents/[a-z-]+\.md|scripts/synthesize\.py)")
        for f in list((ROOT / "skills").glob("*/SKILL.md")) + list((ROOT / "agents").glob("*.md")):
            with self.subTest(f=f.relative_to(ROOT)):
                self.assertEqual(bare.findall(f.read_text()), [])

    def test_skill_fallback_templates_pass_the_output_file_hook(self):
        # K02: 스킬이 처방한 general-purpose 폴백을 하네스 자신의 훅이 막았다. 템플릿을 고칠 때마다
        # 이 테스트가 처방과 집행의 일치를 다시 확인한다.
        import re
        found = 0
        for f in (ROOT / "skills").glob("*/SKILL.md"):
            for line in f.read_text().splitlines():
                m = re.match(r"\s*> (You are running as .*)", line)
                if not m:
                    continue
                found += 1
                prompt = m.group(1).replace("<scratchpad>", "/tmp/sp")
                with self.subTest(f=f.parent.name):
                    self.assertIsNone(run_hook("require-agent-output-file.py", {"tool_name": "Agent", "tool_input": {
                        "subagent_type": "general-purpose", "prompt": prompt}}))
        self.assertGreaterEqual(found, 5)

    def test_hook_output_has_no_unexpanded_plugin_root(self):
        # 훅 출력 텍스트는 치환되지 않는다. 훅이 환경변수로 직접 풀어야 한다.
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "package.json").write_text("{}")
            (Path(d) / "supabase").mkdir()
            out = run_hook("harness-boarding-guard.py", {"cwd": d},
                           env={"CLAUDE_PROJECT_DIR": d, "CLAUDE_PLUGIN_ROOT": "/opt/plugin-root"})
        text = json.dumps(out, ensure_ascii=False)
        self.assertNotIn("${CLAUDE_PLUGIN_ROOT}", text)
        self.assertIn("/opt/plugin-root/docs/BOOTSTRAP.md", text)


class ThirdReviewSelfProtectionTest(unittest.TestCase):
    """3차 리뷰(2026-10-06) 자기보호 결함 회귀 — 설치본 코드·별칭 링크·비활성화 경로, 그리고 과차단."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self.tmp.name)
        (self.proj / "package.json").write_text("{}")
        (self.proj / "supabase").mkdir()
        self.cache = str(Path.home() / ".claude/plugins/cache/react-supabase/react-supabase-harness/0.31.0")

    def tearDown(self):
        self.tmp.cleanup()

    def gate(self, payload):
        return run_hook("gate-engine.py", payload, env={"CLAUDE_PROJECT_DIR": str(self.proj)})

    def bash(self, c):
        return self.gate({"tool_name": "Bash", "tool_input": {"command": c}})

    def test_installed_plugin_code_is_protected(self):
        # 훅 파일 하나를 고치면 모든 게이트가 꺼진다 — 룰 파일만 지키던 결함
        for payload in [
            {"tool_name": "Edit", "tool_input": {"file_path": self.cache + "/hooks/gate-engine.py",
                                                 "old_string": "a", "new_string": "b"}},
            {"tool_name": "Write", "tool_input": {"file_path": self.cache + "/hooks/hooks.json", "content": "{}"}},
            {"tool_name": "Bash", "tool_input": {"command": "chmod -x ~/.claude/plugins/cache/x/hooks/gate-engine.py"}},
            {"tool_name": "Bash", "tool_input": {"command": "echo '{}' > ~/.claude/plugins/cache/x/hooks/hooks.json"}},
        ]:
            with self.subTest(p=json.dumps(payload)[:80]):
                self.assertTrue(denied(self.gate(payload)))

    def test_reading_installed_plugin_and_plugin_data_passes(self):
        for c in ["cat ~/.claude/plugins/cache/x/hooks/hooks.json",
                  "cp ~/.claude/plugins/cache/x/README.md /tmp/readme.md",
                  "grep -r deny ~/.claude/plugins/cache/x/hooks > /tmp/hits.txt"]:
            with self.subTest(c=c):
                self.assertIsNone(self.bash(c))
        self.assertIsNone(self.gate({"tool_name": "Write", "tool_input": {
            "file_path": str(Path.home() / ".claude/plugins/data/some-plugin/state.json"), "content": "{}"}}))

    def test_symlink_alias_cannot_reach_gate_files(self):
        (self.proj / ".claude" / "gates").mkdir(parents=True)
        (self.proj / ".claude" / "gates" / "rules.jsonc").write_text("{}")
        self.assertTrue(denied(self.bash("ln -s .claude cfg")))
        os.symlink(self.proj / ".claude", self.proj / "cfg")       # 사용자가 미리 만든 링크라고 가정
        self.assertTrue(denied(self.gate({"tool_name": "Write", "tool_input": {
            "file_path": str(self.proj / "cfg" / "gates" / "rules.jsonc"), "content": "{}"}})))
        self.assertTrue(denied(self.bash("cp /tmp/empty cfg/gates/rules.jsonc")))

    def test_plugin_cannot_be_disabled_by_agent(self):
        s = self.proj / ".claude" / "settings.json"
        s.parent.mkdir(parents=True)
        s.write_text('{\n  "enabledPlugins": {\n    "react-supabase-harness@react-supabase": true,\n'
                     '    "other@x": true\n  }\n}\n')
        flip = {"tool_name": "Edit", "tool_input": {"file_path": str(s), "old_string": "react-supabase\": true",
                                                    "new_string": "react-supabase\": false"}}
        drop = {"tool_name": "Edit", "tool_input": {
            "file_path": str(s), "old_string": '    "react-supabase-harness@react-supabase": true,\n',
            "new_string": ""}}
        other = {"tool_name": "Edit", "tool_input": {"file_path": str(s), "old_string": '"other@x": true',
                                                     "new_string": '"other@x": false'}}
        self.assertTrue(denied(self.gate(flip)))
        self.assertTrue(denied(self.gate(drop)))
        self.assertIsNone(self.gate(other))                      # 다른 플러그인은 사용자 자유
        for c in ["claude plugin disable react-supabase-harness",
                  "claude plugin uninstall react-supabase-harness@react-supabase"]:
            with self.subTest(c=c):
                self.assertTrue(denied(self.bash(c)))
        self.assertIsNone(self.bash("claude plugin disable some-other-plugin"))

    def test_protected_name_in_read_output_is_not_a_write(self):
        # 과차단 회귀: 보호 이름 언급 + 다른 곳으로의 리다이렉트를 쓰기로 오판했다
        for c in ["rg 'gate-off' hooks/ > /tmp/out.txt",
                  "echo 'see rules.jsonc' > notes.md",
                  "grep -c plan-gate-off docs/*.md > /tmp/count",
                  "npm test 2>&1 | tee test.log  # gate-off tests",
                  "mkdir -p .claude/state"]:
            with self.subTest(c=c):
                self.assertIsNone(self.bash(c))

    def test_output_file_rule_matches_harness_own_workflows(self):
        # K01: /phase 가 처방한 planner 스폰을 하네스 자신이 막았다
        def spawn(t, p):
            return run_hook("require-agent-output-file.py", {"tool_name": "Agent", "tool_input": {
                "subagent_type": t, "prompt": p}})
        self.assertIsNone(spawn("react-supabase-harness:planner",
                                "대상 파일: docs/plans/phase-1-board.md / 모드: create / Tier: Phase"))
        self.assertIsNone(spawn("fork", "Continue."))
        # A40: 읽을 경로 옆의 report 는 산출 지시가 아니고, 면제는 이름 전체 일치만
        for t, p in [("general-purpose", "Read docs/plans/phase-1.md and report back in chat."),
                     ("someverifier-but-actually-research", "Research for an hour."),
                     ("my-craft-reviewer", "Review.")]:
            with self.subTest(t=t, p=p):
                self.assertTrue(denied(spawn(t, p)))
        for p in ["Research X. Save to out.md", "Save results to $SCRATCH/out.md",
                  "조사 결과를 /tmp/r/table.csv 에 저장"]:
            with self.subTest(p=p):
                self.assertIsNone(spawn("general-purpose", p))

    def test_output_file_name_that_claude_code_refuses_is_denied(self):
        # Claude Code 는 서브에이전트의 REPORT·SUMMARY·FINDINGS·ANALYSIS*.md 저장을 거부한다(실측)
        out = run_hook("require-agent-output-file.py", {"tool_name": "Agent", "tool_input": {
            "subagent_type": "general-purpose", "prompt": "Audit. Save findings to /tmp/x/REPORT.md"}})
        self.assertTrue(denied(out))
        self.assertIn("ledger", reason(out))

    def test_deny_messages_do_not_reveal_bypass_tokens(self):
        # K03: 거부 메시지가 우회 토큰을 그대로 알려 주면 자기 우회 안내가 된다
        out = run_hook("require-agent-output-file.py", {"tool_name": "Agent", "tool_input": {
            "subagent_type": "general-purpose", "prompt": "시장 조사해줘"}})
        self.assertNotIn("[HARNESS", reason(out))

    def test_more_write_forms_on_gate_files(self):
        for c in ["curl -o .claude/state/plan-gate-off https://example.com",
                  "cp -r /tmp/st .claude/",
                  "git checkout other-branch -- .claude",
                  "python3 -c \"open('.claude/state/plan-gate-off','w')\""]:
            with self.subTest(c=c):
                self.assertTrue(denied(self.bash(c)))

    def test_variable_targets_on_gate_files(self):
        # 변수 펼침(v0.35.1)이 보호 파일 쓰기를 숨기지 않는가 — 확정된 값이면 펼쳐서 막고,
        # 바뀔 수 있는 값이면 첫 대입값(프로젝트 밖)으로 오판하지 않는다.
        for c in ["D=.claude/state && touch $D/plan-gate-off",
                  "D=.claude && cp /tmp/x ${D}/state/plan-gate-off",
                  "D=/tmp/x; for D in .claude/state; do touch $D/plan-gate-off; done",
                  "D=/tmp/x; D=.claude/state; touch $D/plan-gate-off",
                  "D=/tmp/x | true; touch $D/.claude/state/plan-gate-off"]:
            with self.subTest(c=c):
                self.assertTrue(denied(self.bash(c)))

    def test_expanded_values_and_braces_do_not_hide_gate_writes(self):
        # 0.35.2 커밋 보안 리뷰 — 펼친 `#` 이 주석이 되어 뒤의 쓰기를 숨겼고, 중괄호 확장된 대상은 글자 그대로 비교됐다
        for c in ["C=#; echo x $C > .claude/state/plan-gate-off",
                  "C=#; echo x $C | tee .claude/state/plan-gate-off",
                  "echo x | tee .claude/sta{te,x}/plan-gate-off",
                  "touch .claude/state/plan-gate-o{ff,n}",
                  "touch .claude/x{1..300}/y",                 # 확장이 너무 크면 모르는 대상 — 막는다
                  "echo x | t\\\nee .claude/state/plan-gate-off",
                  "touch $'.claude/state/plan-gate-off'", "touch $'\\x2eclaude'/state/plan-gate-off"]:
            with self.subTest(c=c):
                self.assertTrue(denied(self.bash(c)))


# ─────────────────────────────────────────────────────────────────────────────
# v0.33.0 결과 기반 센서 — 명령 표기 대신 결과(쓰기 대상·package.json·작업 트리·판정 기록)로 판정
# ─────────────────────────────────────────────────────────────────────────────
def _git(cwd, *a, inp=None):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=cwd, capture_output=True,
                          text=True, input=inp)


class _RepoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proj = os.path.realpath(self.tmp.name)
        _git(self.proj, "init", "-q", "-b", "main")
        Path(self.proj, "package.json").write_text(json.dumps({"dependencies": {"zod": "^3"}}))
        Path(self.proj, "supabase").mkdir()
        Path(self.proj, "src").mkdir()
        Path(self.proj, "src", "old.ts").write_text("x\n")
        Path(self.proj, "docs").mkdir()
        Path(self.proj, "docs", "n.md").write_text("n\n")
        _git(self.proj, "add", "-A")
        _git(self.proj, "commit", "-qm", "init")

    def tearDown(self):
        self.tmp.cleanup()

    def hook(self, name, payload):
        return run_hook(name, dict(payload, cwd=self.proj), env={"CLAUDE_PROJECT_DIR": self.proj}, cwd=self.proj)

    def gate(self, tool, ti, event="PreToolUse"):
        return self.hook("gate-engine.py", {"tool_name": tool, "tool_input": ti, "hook_event_name": event})


class PlanFirstShellTest(_RepoCase):
    BIG = "\n".join(f"export const v{i} = {i}" for i in range(40))

    def test_shell_created_code_needs_a_plan(self):
        for c in [f"cat > src/new.ts <<'EOF'\n{self.BIG}\nEOF", f"cd src && cat > f.ts <<'EOF'\n{self.BIG}\nEOF",
                  "cp /tmp/x.ts src/copied.ts", f"cat > src/old.ts <<'EOF'\n{self.BIG}\nEOF"]:
            with self.subTest(c=c[:40]):
                out = self.gate("Bash", {"command": c})
                self.assertTrue(denied(out))
                self.assertIn("다음 행동", reason(out))

    def test_small_edits_deletes_and_outside_paths_pass(self):
        for c in ["sed -i '' 's/x/y/' src/old.ts", "rm src/old.ts", "ls src && cat src/old.ts",
                  f"cat > notes.md <<'EOF'\n{self.BIG}\nEOF", f"cat > /tmp/src/x.ts <<'EOF'\n{self.BIG}\nEOF"]:
            with self.subTest(c=c[:40]):
                self.assertIsNone(self.gate("Bash", {"command": c}))

    def test_variable_targets_resolve_like_the_shell(self):
        # 2026-10-09 실사용: `S=<스크래치> && … > $S/…/x.sql` 이 '프로젝트/$S/…' 로 읽혀 플랜 게이트에 막혔다
        out_tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(out_tmp, ignore_errors=True))
        ok = f"S={out_tmp} && mkdir -p $S/supabase/migrations && cat > $S/supabase/migrations/a.sql <<'EOF'\n{self.BIG}\nEOF"
        self.assertIsNone(self.gate("Bash", {"command": ok}))
        # 같은 명령 안에서 프로젝트를 가리키면 그대로 막는다
        bad = f"D=src && cat > $D/new.ts <<'EOF'\n{self.BIG}\nEOF"
        self.assertTrue(denied(self.gate("Bash", {"command": bad})))

    def test_a_plan_unlocks_shell_writes(self):
        Path(self.proj, "docs", "plans").mkdir()
        Path(self.proj, "docs", "plans", "phase-1.md").write_text("# plan")
        self.assertIsNone(self.gate("Bash", {"command": f"cat > src/new.ts <<'EOF'\n{self.BIG}\nEOF"}))


class BannedPackageFormsTest(_RepoCase):
    LIB = "an" + "td"

    def test_alias_tarball_pkg_set_and_style_imports_are_denied(self):
        b = self.LIB
        for tool, ti in [("Bash", {"command": f"pnpm add ui@npm:{b}"}),
                         ("Bash", {"command": f"npm i https://registry.npmjs.org/{b}/-/{b}-5.0.0.tgz"}),
                         ("Bash", {"command": f"npm i ./{b}-5.1.0.tgz"}),
                         ("Bash", {"command": f"npm pkg set dependencies.{b}=^5"}),
                         ("Write", {"file_path": f"{self.proj}/package.json",
                                    "content": json.dumps({"dependencies": {"ui": f"npm:{b}@^5"}})}),
                         ("Edit", {"file_path": f"{self.proj}/src/old.ts", "old_string": "x",
                                   "new_string": f"import '{b}/dist/reset.css'"}),
                         ("Write", {"file_path": f"{self.proj}/src/index.css", "content": f"@import '{b}/dist/reset.css';"}),
                         ("Write", {"file_path": f"{self.proj}/src/a.scss", "content": f"@use '~{b}/lib/style';"})]:
            with self.subTest(tool=tool, ti=json.dumps(ti)[:60]):
                self.assertTrue(denied(self.gate(tool, ti)))

    def test_ordinary_styles_and_scripts_pass(self):
        for tool, ti in [("Write", {"file_path": f"{self.proj}/src/index.css",
                                    "content": "@import './tokens.css';\n@import 'tailwindcss';"}),
                         ("Edit", {"file_path": f"{self.proj}/src/old.ts", "old_string": "x",
                                   "new_string": "import './index.css'"}),
                         ("Bash", {"command": "npm pkg set scripts.dev=vite"})]:
            with self.subTest(tool=tool):
                self.assertIsNone(self.gate(tool, ti))

    def test_outcome_check_after_any_command(self):
        pj = Path(self.proj, "package.json")
        pj.write_text(json.dumps({"dependencies": {"zod": "^3", "ui": f"npm:{self.LIB}@^5"}}))
        out = self.gate("Bash", {"command": "node scripts/setup.js"}, event="PostToolUse")
        self.assertTrue(blocked(out))
        self.assertIn("다음 행동", out["reason"])
        pj.write_text(json.dumps({"dependencies": {"zod": "^3", "dayjs": "^1"}}))
        self.assertIsNone(self.gate("Bash", {"command": "pnpm add dayjs"}, event="PostToolUse"))

    def test_outcome_check_ignores_committed_deps_and_honors_the_hatch(self):
        pj = Path(self.proj, "package.json")
        pj.write_text(json.dumps({"dependencies": {self.LIB: "^5"}}))
        _git(self.proj, "commit", "-qam", "legacy")
        self.assertIsNone(self.gate("Bash", {"command": "pnpm i"}, event="PostToolUse"))
        pj.write_text(json.dumps({"dependencies": {self.LIB: "^5", "@mui/material": "^5"}}))
        self.assertTrue(blocked(self.gate("Bash", {"command": "pnpm i"}, event="PostToolUse")))
        Path(self.proj, ".claude", "state").mkdir(parents=True)
        Path(self.proj, ".claude", "state", "ui-lib-gate-off").write_text("")
        self.assertIsNone(self.gate("Bash", {"command": "pnpm i"}, event="PostToolUse"))


class SnapshotGuardTest(_RepoCase):
    def snap(self, cmd):
        return self.hook("snapshot-guard.py", {"tool_name": "Bash", "tool_input": {"command": cmd}})

    def snapshots(self):
        return _git(self.proj, "for-each-ref", "--sort=-refname", "--format=%(objectname)",
                    "refs/harness/snapshots/").stdout.split()

    def test_reads_are_skipped_and_unknown_commands_are_snapshotted(self):
        Path(self.proj, "src", "wip.ts").write_text("uncommitted\n")
        self.assertIsNone(self.snap("git status && ls src && git log --oneline -3 && grep -rn x src > /tmp/o"))
        self.assertEqual(self.snapshots(), [])
        out = self.snap("git clean -fd")
        self.assertIn("git show", context(out))
        self.assertEqual(len(self.snapshots()), 1)
        self.snap("eval \"$(echo rm -rf src)\"")              # 모르는 표기 → 찍는다(같은 트리면 재사용)
        self.assertEqual(len(self.snapshots()), 1)
        files = _git(self.proj, "ls-tree", "-r", "--name-only", self.snapshots()[0]).stdout.split()
        self.assertIn("src/wip.ts", files)                       # 미추적 포함
        self.assertEqual(_git(self.proj, "diff", "--cached", "--name-only").stdout, "")  # 인덱스 무변경

    def test_snapshot_restores_after_destruction(self):
        Path(self.proj, "src", "old.ts").write_text("changed\n")
        Path(self.proj, "src", "wip.ts").write_text("new\n")
        self.snap("git reset --hard && git clean -fd")
        _git(self.proj, "reset", "-q", "--hard")
        _git(self.proj, "clean", "-qfd")
        sha = self.snapshots()[0]
        self.assertEqual(_git(self.proj, "show", f"{sha}:src/wip.ts").stdout, "new\n")
        self.assertEqual(_git(self.proj, "show", f"{sha}:src/old.ts").stdout, "changed\n")

    def test_snapshot_follows_cd_and_stays_inside_the_project(self):
        inner = Path(self.proj, "pkgs", "inner")
        inner.mkdir(parents=True)
        _git(str(inner), "init", "-q")
        Path(inner, "a.txt").write_text("1")
        _git(str(inner), "add", "-A")
        _git(str(inner), "commit", "-qm", "i")
        self.snap("cd pkgs/inner && rm a.txt")                       # `cd` 뒤의 저장소를 찍는다
        self.assertEqual(len(_git(str(inner), "for-each-ref", "refs/harness/").stdout.split("\n")) - 1, 1)
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        _git(outside.name, "init", "-q")
        Path(outside.name, "b.txt").write_text("1")
        self.snap(f"cd {outside.name} && rm b.txt")                    # 프로젝트 밖 저장소에는 ref 를 쓰지 않는다
        self.assertEqual(_git(outside.name, "for-each-ref", "refs/harness/").stdout, "")

    def test_push_hook_skipping_is_denied(self):
        for c in ["git push --no-verify origin main", "git -c core.hooksPath=/dev/null push origin main"]:
            with self.subTest(c=c):
                self.assertTrue(denied(run_hook("block-destructive-git.py",
                                                {"tool_name": "Bash", "tool_input": {"command": c}})))


class PrePushHookTest(_RepoCase):
    def setUp(self):
        super().setUp()
        self.bare = tempfile.TemporaryDirectory()
        _git(self.bare.name, "init", "-q", "--bare")
        _git(self.proj, "remote", "add", "origin", self.bare.name)
        _git(self.proj, "push", "-q", "origin", "main")
        r = subprocess.run(["sh", str(ROOT / "scripts" / "install-git-hooks.sh")], cwd=self.proj,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def tearDown(self):
        self.bare.cleanup()
        super().tearDown()

    def push(self, *a):
        return _git(self.proj, "push", "-q", *a)

    def test_default_branch_rewrite_and_remote_delete_are_refused(self):
        Path(self.proj, "x.txt").write_text("1")
        _git(self.proj, "add", "x.txt")
        _git(self.proj, "commit", "-qm", "ff")
        self.assertEqual(self.push("origin", "main").returncode, 0)
        _git(self.proj, "reset", "-q", "--hard", "HEAD~1")
        _git(self.proj, "commit", "-q", "--allow-empty", "-m", "rewrite")
        p = self.push("--force", "origin", "main")
        self.assertEqual(p.returncode, 1)
        self.assertIn("non-fast-forward", p.stderr)
        self.assertEqual(self.push("origin", "+main:main").returncode, 1)
        _git(self.proj, "checkout", "-q", "-b", "feat")
        self.assertEqual(self.push("origin", "feat").returncode, 0)
        _git(self.proj, "commit", "-q", "--amend", "-m", "amended")
        self.assertEqual(self.push("--force-with-lease", "origin", "feat").returncode, 0)   # 기능 브랜치 rebase
        self.assertEqual(self.push("origin", "--delete", "feat").returncode, 1)

    def test_existing_hook_is_kept_and_chained(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        _git(other.name, "init", "-q")
        hook = Path(other.name, ".git", "hooks", "pre-push")
        hook.write_text("#!/bin/sh\necho LOCAL-RAN >&2\nexit 0\n")
        hook.chmod(0o755)
        subprocess.run(["sh", str(ROOT / "scripts" / "install-git-hooks.sh")], cwd=other.name, capture_output=True)
        self.assertTrue(Path(other.name, ".git", "hooks", "pre-push.local").is_file())
        r = subprocess.run([str(hook), "origin", "x"], cwd=other.name, input="", capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("LOCAL-RAN", r.stderr)


class SubagentAuditTest(_RepoCase):
    def audit(self, event, agent="general-purpose", aid="ag1", msg="", active=False):
        return self.hook("subagent-audit.py", {"hook_event_name": event, "agent_id": aid, "agent_type": agent,
                                               "session_id": "s1", "last_assistant_message": msg,
                                               "stop_hook_active": active})

    def test_code_zone_side_effects_stop_the_subagent_once(self):
        self.assertIsNone(self.audit("SubagentStart"))
        Path(self.proj, "src", "old.ts").write_text("x;\n")          # 포매터 흉내
        Path(self.proj, "docs", "n.md").write_text("edited\n")
        out = self.audit("SubagentStop")
        self.assertTrue(blocked(out))
        self.assertIn("src/old.ts", out["reason"])
        self.assertNotIn("docs/n.md", out["reason"])
        self.assertIn("다음 행동", out["reason"])
        self.assertIsNone(self.audit("SubagentStop", active=True))

    def test_research_only_and_writer_types_pass(self):
        self.audit("SubagentStart", aid="r")
        Path(self.proj, "docs", "n.md").write_text("edited\n")
        self.assertIsNone(self.audit("SubagentStop", aid="r"))
        Path(self.proj, ".claude", "gates").mkdir(parents=True)
        Path(self.proj, ".claude", "gates", "rules.jsonc").write_text('{"delegation": {"writers": ["implementer"]}}')
        self.audit("SubagentStart", agent="my:implementer", aid="w")
        Path(self.proj, "src", "old.ts").write_text("y\n")
        self.assertIsNone(self.audit("SubagentStop", agent="my:implementer", aid="w"))

    def test_hook_written_verdicts_gate_the_approved_commit(self):
        commit = {"tool_name": "Bash", "tool_input": {"command": "CLAUDE_COMMIT_APPROVED=1 git commit -F /tmp/m"}}
        self.audit("SubagentStop", agent="react-supabase-harness:verifier", aid="v1", msg="## Verdict\nFAIL")
        out = self.hook("block-destructive-git.py", commit)
        self.assertTrue(denied(out))
        self.assertIn("/verify", reason(out))
        self.audit("SubagentStop", agent="react-supabase-harness:verifier", aid="v2", msg="## Verdict\nPASS")
        self.assertIsNone(self.hook("block-destructive-git.py", commit))
        self.audit("SubagentStop", agent="react-supabase-harness:structure-fitness-reviewer", aid="s",
                   msg="Critical 0 / High 1\nVERDICT(GAP 2)")
        self.audit("SubagentStop", agent="Explore", aid="e", msg="## Verdict\nFAIL")   # 하네스 에이전트만 기록
        recs = [json.loads(x) for x in Path(self.proj, ".claude/state/verdicts.jsonl").read_text().splitlines()]
        self.assertEqual([r["verdict"] for r in recs], ["FAIL", "PASS", "GAP"])
        self.assertEqual(recs[-1]["critical"], 0)

    def test_verdict_file_is_agent_protected(self):
        vf = f"{self.proj}/.claude/state/verdicts.jsonl"
        self.assertTrue(denied(self.gate("Write", {"file_path": vf, "content": "{}"})))
        self.assertTrue(denied(self.gate("Bash", {"command": "echo '{}' >> .claude/state/verdicts.jsonl"})))
        self.assertIsNone(self.gate("Bash", {"command": "cat .claude/state/verdicts.jsonl"}))


class SnapshotPostNoticeTest(_RepoCase):
    """명령 문자열로는 삭제가 안 보이는 경우(스크립트 안의 git clean)도 결과로 잡아 복구 경로를 준다."""

    def snap(self, cmd, event="PreToolUse"):
        return self.hook("snapshot-guard.py", {"tool_name": "Bash", "tool_input": {"command": cmd},
                                               "hook_event_name": event})

    def test_deleted_files_are_reported_after_the_command(self):
        Path(self.proj, "src", "wip.ts").write_text("draft\n")
        self.assertIsNone(self.snap("./reset.sh"))                  # LOSSY 아님 → 사전 알림 없음
        Path(self.proj, "src", "wip.ts").unlink()
        out = self.snap("./reset.sh", "PostToolUse")
        self.assertIn("src/wip.ts", context(out))
        self.assertIn("show", context(out))
        self.assertIsNone(self.snap("./reset.sh", "PostToolUse"))   # 표식은 한 번만 쓰인다

    def test_no_notice_without_deletion_or_for_another_command(self):
        Path(self.proj, "src", "wip.ts").write_text("draft\n")
        self.snap("./build.sh")
        Path(self.proj, "src", "new.ts").write_text("added\n")     # 추가·수정은 알리지 않는다
        self.assertIsNone(self.snap("./build.sh", "PostToolUse"))
        self.snap("./a.sh")
        Path(self.proj, "src", "wip.ts").unlink()
        self.assertIsNone(self.snap("./b.sh", "PostToolUse"))        # 다른 명령의 표식으로 판단하지 않는다


class SnapshotPrivacyTest(_RepoCase):
    """스냅샷은 미추적 비밀 파일·대용량을 빼고, 명령 원문을 남기지 않고, 뺀 파일이 사라지면 복구 불가를 알린다."""

    def snap(self, cmd, event="PreToolUse"):
        return self.hook("snapshot-guard.py", {"tool_name": "Bash", "tool_input": {"command": cmd},
                                               "hook_event_name": event})

    def latest(self):
        return _git(self.proj, "for-each-ref", "--sort=-refname", "--count=1", "--format=%(objectname)",
                    "refs/harness/snapshots/").stdout.strip()

    def files(self, sha):
        return set(_git(self.proj, "ls-tree", "-r", "--name-only", sha).stdout.split())

    def test_untracked_secrets_and_big_files_are_left_out_by_exact_name(self):
        for p in (".env", ".env.local", ".env.example", "certs/key.pem", "credentials.json", "id_rsa",
                  "gcp/service-account-prod.json", "src/secret_utils.ts", "src/credentials.ts", "keys.md"):
            Path(self.proj, p).parent.mkdir(parents=True, exist_ok=True)
            Path(self.proj, p).write_text("x\n")
        Path(self.proj, "big.bin").write_bytes(b"\0" * (5 * 1024 * 1024 + 1))
        Path(self.proj, "ok.bin").write_bytes(b"\0" * 1024)
        self.snap("./x.sh")
        files = self.files(self.latest())
        for kept in (".env.example", "src/secret_utils.ts", "src/credentials.ts", "keys.md", "ok.bin"):
            self.assertIn(kept, files)                   # 부분 일치로 코드 파일을 삼키지 않는다
        for gone in (".env", ".env.local", "certs/key.pem", "credentials.json", "id_rsa",
                     "gcp/service-account-prod.json", "big.bin"):
            self.assertNotIn(gone, files)

    def test_tracked_secret_stays_because_it_is_already_in_history(self):
        Path(self.proj, ".env").write_text("A=1\n")
        _git(self.proj, "add", ".env")
        _git(self.proj, "commit", "-qm", "oops")
        Path(self.proj, ".env").write_text("A=2\n")
        self.snap("./x.sh")
        self.assertIn(".env", self.files(self.latest()))  # 빼면 스냅샷에서 삭제로 보인다

    def test_message_carries_no_command_text(self):
        Path(self.proj, "src", "wip.ts").write_text("w\n")
        self.snap("curl -H 'Authorization: tok-should-not-persist' https://x")
        msg = _git(self.proj, "log", "-1", "--format=%B", self.latest()).stdout
        self.assertNotIn("tok-should-not-persist", msg)
        self.assertNotIn("curl", msg)

    def test_lost_excluded_file_is_reported_as_unrecoverable(self):
        Path(self.proj, ".env.local").write_text("S=1\n")
        Path(self.proj, "src", "wip.ts").write_text("w\n")
        self.snap("./reset.sh")
        Path(self.proj, ".env.local").unlink()
        out = self.snap("./reset.sh", "PostToolUse")
        self.assertIn(".env.local", context(out))
        self.assertIn("복구 불가", context(out))

    def test_purge_script_is_dry_run_by_default(self):
        Path(self.proj, "src", "wip.ts").write_text("w\n")
        self.snap("./x.sh")
        self.assertTrue(self.latest())
        script = str(ROOT / "scripts" / "purge-snapshots.py")
        r = subprocess.run([sys.executable, script, self.proj], capture_output=True, text=True)
        self.assertIn("dry-run", r.stdout)
        self.assertTrue(self.latest())
        r = subprocess.run([sys.executable, script, "--apply", self.proj], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.latest(), "")


class VerdictGateTest(_RepoCase):
    """훅이 쓴 판정 기록을 다음 단계가 읽는다 — phase 0 FIT 없이 phase 1 plan 금지, Critical 위 PR 금지."""

    def record(self, agent, msg, aid):
        self.hook("subagent-audit.py", {"hook_event_name": "SubagentStop", "agent_id": aid,
                                        "agent_type": f"react-supabase-harness:{agent}", "session_id": "s1",
                                        "last_assistant_message": msg, "stop_hook_active": False})

    def plan(self):
        return self.gate("Write", {"file_path": f"{self.proj}/docs/plans/phase-1-auth.md", "content": "# p1"})

    def test_phase_plan_needs_a_fit_record_once_phase_0_exists(self):
        self.assertIsNone(self.plan())                                # phase 0 산출물 없는 프로젝트는 판단 밖
        Path(self.proj, "docs", "plans").mkdir()
        Path(self.proj, "docs", "plans", "phase-0-domain.md").write_text("# p0\n")
        out = self.plan()
        self.assertTrue(denied(out))
        self.assertIn("다음 행동", reason(out))
        self.record("structure-fitness-reviewer", "Critical 0 / High 2\nVERDICT(GAP 2)", "s1")
        self.assertTrue(denied(self.plan()))
        self.record("structure-fitness-reviewer", "Critical 0 / High 0\nVERDICT(FIT)", "s2")
        self.assertIsNone(self.plan())
        self.assertIsNone(self.gate("Write", {"file_path": f"{self.proj}/docs/plans/phase-0-domain.md",
                                              "content": "# p0 v2"}))

    def test_pr_is_blocked_while_a_critical_sits_on_head(self):
        pr = {"command": "gh pr create --fill"}
        self.assertIsNone(self.gate("Bash", pr))
        self.record("stability-reviewer", "Critical 1 / High 0\nVERDICT(GAP 1)", "r1")
        out = self.gate("Bash", pr)
        self.assertTrue(denied(out))
        self.assertIn("다음 행동", reason(out))
        self.assertTrue(denied(self.gate("Bash", {"command": "gh pr merge 3 --squash"})))
        self.assertIsNone(self.gate("Bash", {"command": "gh pr view 3"}))
        self.assertIsNone(self.gate("Bash", {"command": "echo 'gh pr create' > notes.txt"}))  # 문자열은 데이터
        Path(self.proj, "src", "old.ts").write_text("fixed\n")
        _git(self.proj, "commit", "-qam", "fix")                      # HEAD 가 바뀌면 그 기록은 판단 밖
        self.assertIsNone(self.gate("Bash", pr))


class CommitAskRuleNoticeTest(unittest.TestCase):
    def test_notice_until_an_ask_rule_exists(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "package.json").write_text("{}")
            home = Path(d, "home")
            home.mkdir()
            env = {"CLAUDE_PROJECT_DIR": d, "HOME": str(home)}
            self.assertIn("Bash(git commit *)", context(run_hook("session-start-summary.py", {}, env=env, cwd=d)))
            Path(d, ".claude").mkdir()
            Path(d, ".claude", "settings.json").write_text(json.dumps({"permissions": {"ask": ["Bash(git commit *)"]}}))
            # 비관할 + 알림 해소 = 할 말이 없다(침묵). 알림 자체는 T0 라 비관할에서도 나왔다(위 단언).
            out = run_hook("session-start-summary.py", {}, env=env, cwd=d)
            self.assertTrue(out is None or "Bash(git commit *)" not in context(out))


if __name__ == "__main__":
    unittest.main()


# ─────────────────────────────────────────────────────────────────────────────
# 센서 메시지 계약 — 모든 차단은 "무엇이 막혔나 / 왜 / 다음 행동" 을 담는다(린터 메시지에 고치는 법)
# ─────────────────────────────────────────────────────────────────────────────
class DenyMessageContractTest(unittest.TestCase):
    def test_every_deny_carries_a_next_action(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "package.json").write_text("{}")
            (p / "supabase").mkdir()
            env = {"CLAUDE_PROJECT_DIR": str(p)}
            big = "\n".join(f"export const v{i} = {i}" for i in range(40))
            cases = {
                "no-ui-library": run_hook("gate-engine.py", {"tool_name": "Bash",
                                          "tool_input": {"command": ADD_UI_LIB}}, env=env),
                "plan-first": run_hook("gate-engine.py", {"tool_name": "Write", "tool_input": {
                                       "file_path": str(p / "src/a.ts"), "content": big}}, env=env),
                "self-protection": run_hook("gate-engine.py", {"tool_name": "Write", "tool_input": {
                                            "file_path": str(p / ".claude/state/plan-gate-off"), "content": ""}}, env=env),
                "destructive-git": run_hook("block-destructive-git.py", {"tool_name": "Bash",
                                            "tool_input": {"command": "git reset --hard HEAD~1"}}),
                "direct-commit": run_hook("block-destructive-git.py", {"tool_name": "Bash",
                                          "tool_input": {"command": "git commit -m x"}}),
                "rm-home": run_hook("block-destructive-git.py", {"tool_name": "Bash",
                                    "tool_input": {"command": "rm -rf ~/cache"}}),
                "delegation": run_hook("block-impl-delegation.py", {"tool_name": "Write", "agent_id": "a1",
                                       "agent_type": "general-purpose", "cwd": str(p),
                                       "tool_input": {"file_path": str(p / "src/x.ts"), "content": "x"}}, env=env),
                "output-file": run_hook("require-agent-output-file.py", {"tool_name": "Agent", "tool_input": {
                                        "subagent_type": "general-purpose", "prompt": "시장 조사해줘"}}),
            }
            for name, out in cases.items():
                with self.subTest(gate=name):
                    self.assertTrue(denied(out), f"{name} 이 막히지 않았다 — 픽스처 확인")
                    self.assertIn("다음 행동", reason(out))


class SessionSummaryPathTest(unittest.TestCase):
    def test_names_project_and_plugin_root_apart(self):
        # 회귀(eval): 소형 모델이 스킬에 펼쳐진 하네스 절대경로를 작업 대상으로 착각해 그쪽으로 cd 했다
        with tempfile.TemporaryDirectory() as d:
            mark_harness(d)
            out = run_hook("session-start-summary.py", {}, env={"CLAUDE_PROJECT_DIR": d}, cwd=d)
            ctx = context(out)
            self.assertIn(f"작업 대상 = {d}", ctx)
            self.assertIn("작업·리뷰 대상이 아니다", ctx)


# ─────────────────────────────────────────────────────────────────────────────
# 4차 적대 리뷰(v0.32.0 수정분 재검증) 회귀 — 우회는 막히고, 과차단은 풀린 채로
# ─────────────────────────────────────────────────────────────────────────────
class FourthReviewRegressionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proj = Path(self.tmp.name)
        (self.proj / "package.json").write_text("{}")
        (self.proj / "supabase").mkdir()
        (self.proj / "src" / "feat").mkdir(parents=True)
        (self.proj / ".claude").mkdir()
        self.settings = self.proj / ".claude" / "settings.json"
        self.settings.write_text(json.dumps({"enabledPlugins": {"react-supabase-harness@react-supabase": True},
                                             "permissions": {"allow": []}}))

    def tearDown(self):
        self.tmp.cleanup()

    def gate(self, tool, ti):
        return run_hook("gate-engine.py", {"tool_name": tool, "tool_input": ti},
                        env={"CLAUDE_PROJECT_DIR": str(self.proj)}, cwd=str(self.proj))

    def sub(self, tool, ti):
        return run_hook("block-impl-delegation.py", {"tool_name": tool, "tool_input": ti, "agent_id": "a1",
                                                     "agent_type": "general-purpose", "cwd": str(self.proj)},
                        env={"CLAUDE_PROJECT_DIR": str(self.proj)})

    def test_settings_rewrites_that_drop_the_harness_are_denied(self):
        s = str(self.settings)
        for tool, ti in [("Write", {"file_path": s, "content": "{}"}),
                         ("MultiEdit", {"file_path": s, "edits": [{"old_string": "true", "new_string": "false"}]}),
                         ("Bash", {"command": "jq 'del(.enabledPlugins)' .claude/settings.json | sponge .claude/settings.json"}),
                         ("Bash", {"command": "jq . .claude/settings.json > /tmp/s && mv /tmp/s .claude/settings.json"}),
                         ("Bash", {"command": "rm .claude/settings.json"}),
                         ("Bash", {"command": "git checkout -- .claude/settings.json"}),
                         ("Bash", {"command": "rm -rf ~/.claude/plugins"}),
                         ("Bash", {"command": "mv ~/.claude/plugins ~/.claude/plugins.bak"})]:
            with self.subTest(tool=tool, ti=json.dumps(ti)[:60]):
                out = self.gate(tool, ti)
                self.assertTrue(denied(out))
                self.assertIn("다음 행동", reason(out))

    def test_settings_edits_that_keep_the_harness_pass(self):
        keep = json.dumps({"enabledPlugins": {"react-supabase-harness@react-supabase": True},
                           "permissions": {"allow": ["Bash(ls)"]}})
        for tool, ti in [("Write", {"file_path": str(self.settings), "content": keep}),
                         ("Edit", {"file_path": str(self.settings), "old_string": '"allow": []',
                                   "new_string": '"allow": ["Bash(ls)"]'}),
                         ("Bash", {"command": "cat .claude/settings.json > /tmp/settings-copy.json"}),
                         ("Bash", {"command": "ls ~/.claude/plugins"})]:
            with self.subTest(tool=tool):
                self.assertIsNone(self.gate(tool, ti))

    def test_data_in_heredocs_and_quotes_is_not_a_command(self):
        for c in ["cat > /tmp/ledger.md <<'EOF'\n| N1 | rm .claude/state/plan-gate-off was mentioned |\nEOF",
                  "echo 'a -> .claude/state/x' > /tmp/n.txt"]:
            with self.subTest(c=c[:40]):
                self.assertIsNone(self.gate("Bash", {"command": c}))
        # 셸이 읽는 heredoc 과 bash -c 인자는 실행된다
        self.assertTrue(denied(self.gate("Bash", {"command": "bash <<'EOF'\ntouch .claude/state/plan-gate-off\nEOF"})))
        self.assertTrue(denied(self.gate("Bash", {"command": "bash -c 'touch .claude/state/ui-lib-gate-off'"})))

    def test_subagent_common_write_forms_are_denied(self):
        for c in ["cd src && cat > a.ts <<'EOF'\nexport {}\nEOF", "(cd src; touch new.ts)", "cp /tmp/a.ts src/",
                  "find src -name '*.ts' -exec sed -i '' 's/a/b/' {} +", "grep -rl foo src | xargs sed -i '' 's/a/b/'",
                  "dd if=/tmp/a of=src/a.ts", "git switch main", "git stash", "git clean -fd src",
                  "git -c core.x=y checkout -- src"]:
            with self.subTest(c=c[:40]):
                self.assertTrue(denied(self.sub("Bash", {"command": c})))
        for f in ["index.html", "tailwind.config.ts", "theme/tokens.css"]:
            with self.subTest(f=f):
                self.assertTrue(denied(self.sub("Write", {"file_path": str(self.proj / f), "content": "x"})))

    def test_subagent_reads_and_other_repos_pass(self):
        for c in ["rg 'git checkout' hooks/", 'grep -n "git reset" src/App.tsx', "git -C /tmp/other checkout main",
                  "git stash list", "echo 'a -> src/App.tsx' > /tmp/n.txt",
                  "cat > /tmp/r/ledger.md <<'EOF'\n| F1 | src/App.tsx | rm src/legacy.ts |\nEOF"]:
            with self.subTest(c=c[:40]):
                self.assertIsNone(self.sub("Bash", {"command": c}))

    def test_monorepo_install_forms_and_unrelated_segments(self):
        lib = "an" + "td"
        for c in [f"pnpm -F web add {lib}", f"yarn workspace web add {lib}", f"npm --prefix web install {lib}",
                  f"npm in {lib}", f"deno add npm:{lib}", "pnpx shadcn init"]:
            with self.subTest(c=c):
                self.assertTrue(denied(self.gate("Bash", {"command": c})))
        for c in [f"pnpm add zod && grep -r {lib} src/", "pnpm -F web add zod"]:
            with self.subTest(c=c):
                self.assertIsNone(self.gate("Bash", {"command": c}))

    def test_plan_first_covers_the_code_zone_directories(self):
        big = "\n".join(f"export const v{i} = {i}" for i in range(40))
        for f in ["app/page.tsx", "components/Button.tsx", "supabase/migrations/001_init.sql"]:
            with self.subTest(f=f):
                self.assertTrue(denied(self.gate("Write", {"file_path": str(self.proj / f), "content": big})))

    def test_output_file_exemptions_are_exact(self):
        def agent(t, p):
            return run_hook("require-agent-output-file.py", {"tool_name": "Agent",
                                                             "tool_input": {"subagent_type": t, "prompt": p}})
        self.assertTrue(denied(agent("other-plugin:explore", "find it")))
        self.assertIsNone(agent("react-supabase-harness:verifier", "run"))
        self.assertIsNone(agent("general-purpose", "research and save to /tmp/r/report-ledger.md"))


# ─────────────────────────────────────────────────────────────────────────────
# v0.36.0 실행 시점 rm 가드 — SessionStart 가 bin/rm 을 PATH 앞에 둔다(CLAUDE_ENV_FILE)
# ─────────────────────────────────────────────────────────────────────────────
class RuntimeRmGuardInstallTest(unittest.TestCase):
    def test_session_start_puts_the_wrapper_first_on_path_once(self):
        with tempfile.TemporaryDirectory() as d:
            env_file = os.path.join(d, "env")
            proj = os.path.join(d, "proj")
            os.makedirs(proj)
            for _ in range(2):                                   # 재개·압축으로 다시 불려도 한 번만
                self.assertIsNone(run_hook("install-rm-guard.py", {"cwd": proj}, env={"CLAUDE_ENV_FILE": env_file}))
            text = open(env_file).read()
            self.assertEqual(text.count("export PATH="), 1, text)
            self.assertIn(os.path.join(str(ROOT), "bin"), text)
            self.assertIn("HARNESS_PROJECT_DIR=" + os.path.realpath(proj), text)
            # 실제 bash 에서 적용되는가 — env 파일을 앞에 실행한 뒤 rm 이 래퍼로 풀린다
            out = subprocess.run(["bash", "-c", f". {env_file}; command -v rm"], capture_output=True, text=True).stdout
            self.assertEqual(out.strip(), os.path.join(str(ROOT), "bin", "rm"))

    def test_stale_wrapper_lines_from_other_versions_are_dropped(self):
        # 실측: 재개한 세션에 옛 버전(0.36.1)이 다른 env 파일에 남긴 줄이 나중에 실행되어 새 래퍼를 가렸다
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d, "cache", "react-supabase-harness")
            for v in ("0.36.1", "0.38.0"):
                (cache / v / "bin").mkdir(parents=True)
                shutil.copy(ROOT / "bin" / "rm", cache / v / "bin" / "rm")
                (cache / v / ".claude-plugin").mkdir()
                (cache / v / ".claude-plugin" / "plugin.json").write_text('{"name": "react-supabase-harness"}')
            other = Path(d, "cache", "other-plugin", "1.0", "bin")
            other.mkdir(parents=True)
            sess = Path(d, "session-env")
            sess.mkdir()
            old = f'export PATH={cache / "0.36.1" / "bin"}:"$PATH"\n'
            keep = f'export PATH={other}:"$PATH"\nexport FOO=1\n'
            (sess / "sessionstart-hook-5.sh").write_text(old)
            (sess / "sessionstart-hook-7.sh").write_text(old + keep)
            new = str(cache / "0.38.0")
            run_hook("install-rm-guard.py", {"cwd": d},
                     env={"CLAUDE_ENV_FILE": str(sess / "sessionstart-hook-5.sh"), "CLAUDE_PLUGIN_ROOT": new})
            self.assertNotIn("0.36.1", (sess / "sessionstart-hook-7.sh").read_text())
            self.assertIn(keep, (sess / "sessionstart-hook-7.sh").read_text())        # 남의 줄은 그대로
            src = "; ".join(f". {p}" for p in sorted(sess.glob("*.sh")))
            out = subprocess.run(["bash", "-c", f"{src}; command -v rm"], capture_output=True, text=True).stdout
            self.assertEqual(out.strip(), str(cache / "0.38.0" / "bin" / "rm"))

    def test_stale_line_cleanup_spares_dev_checkouts_and_handles_broken_manifests(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d, "cache", "mkt", "react-supabase-harness")
            (cache / "0.30.0" / "bin").mkdir(parents=True)                   # plugin.json 이 깨진 옛 버전
            (cache / "0.30.0" / ".claude-plugin").mkdir()
            (cache / "0.30.0" / ".claude-plugin" / "plugin.json").write_text("{broken")
            dev = Path(d, "Projects", "react-supabase-harness")              # 개발 설치(현재)
            fork = Path(d, "Projects", "harness-fork")                       # 같은 폴더의 다른 체크아웃
            for p in (dev, fork):
                (p / "bin").mkdir(parents=True)
                shutil.copy(ROOT / "bin" / "rm", p / "bin" / "rm")
                (p / ".claude-plugin").mkdir()
                (p / ".claude-plugin" / "plugin.json").write_text('{"name": "react-supabase-harness"}')
            sess = Path(d, "session-env")
            sess.mkdir()
            old = f'export PATH={cache / "0.30.0" / "bin"}:"$PATH"\n'
            forkline = f'export PATH={fork / "bin"}:"$PATH"\n'
            (sess / "sessionstart-hook-3.sh").write_text(old + forkline)
            run_hook("install-rm-guard.py", {"cwd": d},
                     env={"CLAUDE_ENV_FILE": str(sess / "sessionstart-hook-9.sh"), "CLAUDE_PLUGIN_ROOT": str(dev)})
            text = (sess / "sessionstart-hook-3.sh").read_text()
            self.assertNotIn("0.30.0", text)
            self.assertIn(forkline, text)

    def test_session_start_without_env_file_is_a_no_op(self):
        self.assertIsNone(run_hook("install-rm-guard.py", {"cwd": "/tmp"}))

    def test_wrapper_blocks_and_explains(self):
        with tempfile.TemporaryDirectory() as d:
            home = os.path.realpath(d)
            env = {"PATH": str(ROOT / "bin") + ":/usr/bin:/bin", "HOME": home,
                   "HARNESS_REAL_RM": "/usr/bin/true", "HARNESS_PROJECT_DIR": home}
            p = subprocess.run(["bash", "-c", 'X=; rm -rf "$X"/'], capture_output=True, text=True, env=env, cwd=home)
            self.assertEqual(p.returncode, 1)
            self.assertIn("다음 행동", p.stderr)
            ok = subprocess.run(["bash", "-c", "rm -rf build/out"], capture_output=True, text=True, env=env, cwd=home)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            # 0.36.0 커밋 보안 리뷰: 긴 옵션 축약(`--rec`)·모르는 긴 옵션도 재귀로 보고 대상을 검사한다
            for flags in ("--recursive", "--rec", "--r", "--force --recurs", "--unknown-opt"):
                with self.subTest(flags=flags):
                    p = subprocess.run(["bash", "-c", f'X=; rm {flags} "$X"/'], capture_output=True, text=True,
                                       env=env, cwd=home)
                    self.assertEqual(p.returncode, 1, p.stderr)


# ─────────────────────────────────────────────────────────────────────────────
# 관할(hooks/scope.py) — T0 는 어디서나, T1 은 하네스 관할 프로젝트에서만. 같은 입력의 쌍으로 본다.
# ─────────────────────────────────────────────────────────────────────────────
def _scope():
    spec = importlib.util.spec_from_file_location("scope", HOOKS / "scope.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ScopeTest(unittest.TestCase):
    def setUp(self):
        self.scope = _scope()
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(os.path.realpath(self.tmp.name))
        self.plain = base / "plain"                                       # 하네스와 무관한 레포
        self.harness = base / "harness"
        self.plain.mkdir()
        self.harness.mkdir()
        mark_harness(self.harness)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fixture_is_in_jurisdiction(self):
        # 다른 테스트의 T1 단언이 전부 이 픽스처에 기댄다 — 픽스처가 관할을 잃으면 여기서 먼저 깨진다
        self.assertEqual(self.scope.kind(Path(HARNESS_FIXTURE)), "harness")
        self.assertEqual(self.scope.kind(self.harness), "harness")
        self.assertEqual(self.scope.kind(self.plain), "other")

    def test_every_registered_hook_has_exactly_one_tier(self):
        cmds = [h["command"] for groups in json.loads((HOOKS / "hooks.json").read_text())["hooks"].values()
                for g in groups for h in g["hooks"]]
        names = {Path(c.split("/hooks/", 1)[1].rstrip('"')).stem for c in cmds}
        stems = {}
        for k in self.scope.TIERS:
            stems.setdefault(k.split(".", 1)[0], []).append(k)
        self.assertEqual(names, set(stems), "hooks.json 과 scope.TIERS 가 어긋났다")
        for stem, parts in stems.items():
            with self.subTest(hook=stem):
                # 부분으로 나눈 훅은 부분만, 나누지 않은 훅은 이름 하나만 — 섞으면 어느 쪽이 이기는지 모호하다
                self.assertTrue(parts == [stem] or all("." in p for p in parts), parts)
        self.assertRaises(KeyError, self.scope.applies, "no-such-hook", self.plain)

    def test_kind_by_marker_and_structure(self):
        d = Path(self.tmp.name)
        for name, setup, want in [
            ("marker-only", lambda p: mark_harness(p), "harness"),
            ("proto", lambda p: (p / ".harness.json").write_text('{"role": "prototype"}'), "prototype"),
            ("tmpl", lambda p: (p / ".harness.json").write_text('{"role": "template"}'), "template"),
            ("supa", lambda p: ((p / "package.json").write_text("{}"), (p / "supabase").mkdir()), "harness"),
            ("pkg-only", lambda p: (p / "package.json").write_text("{}"), "other"),
            ("supa-proto", lambda p: ((p / "package.json").write_text("{}"), (p / "supabase").mkdir(),
                                      (p / ".harness.json").write_text('{"role": "prototype"}')), "prototype"),
            ("other-plugin", lambda p: ((p / ".claude-plugin").mkdir(), (p / ".claude-plugin" / "plugin.json")
                                        .write_text('{"name": "x"}'), mark_harness(p)), "other"),
        ]:
            with self.subTest(name=name):
                p = d / name
                p.mkdir()
                setup(p)
                self.assertEqual(self.scope.kind(p), want)
        self.assertEqual(self.scope.kind(None), "other")
        self.assertEqual(self.scope.kind(ROOT), "self")

    def test_project_root_prefers_session_project_then_payload(self):
        env = os.environ.pop("CLAUDE_PROJECT_DIR", None)
        try:
            self.assertIsNone(self.scope.project_root({}))
            self.assertEqual(self.scope.project_root({"cwd": str(self.plain)}), self.plain)
            os.environ["CLAUDE_PROJECT_DIR"] = str(self.harness)
            self.assertEqual(self.scope.project_root({"cwd": str(self.plain)}), self.harness)
        finally:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
            if env is not None:
                os.environ["CLAUDE_PROJECT_DIR"] = env

    def _pair(self, hook, payload_for, *, extra_env=None):
        out = {}
        for k, root in (("harness", self.harness), ("plain", self.plain)):
            env = dict(extra_env or {}, CLAUDE_PROJECT_DIR=str(root))
            out[k] = run_hook(hook, payload_for(root), env=env, cwd=str(root))
        return out

    def test_t1_hooks_fire_in_jurisdiction_and_stay_silent_outside(self):
        cases = {
            "block-impl-delegation.py": lambda r: {"tool_name": "Write", "agent_id": "a1", "agent_type": "general-purpose",
                                                   "cwd": str(r), "tool_input": {"file_path": str(r / "src/x.ts"), "content": "x"}},
            "require-agent-output-file.py": lambda r: {"tool_name": "Agent", "cwd": str(r), "tool_input": {
                                                       "subagent_type": "general-purpose", "prompt": "시장 조사해줘"}},
        }
        for hook, payload in cases.items():
            with self.subTest(hook=hook):
                out = self._pair(hook, payload)
                self.assertTrue(denied(out["harness"]), hook)
                self.assertIsNone(out["plain"], hook)

    def test_commit_boundary_is_t1(self):
        for root in (self.harness, self.plain):
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".gitignore").write_text(".harness.json\n")
            for i in range(30):
                (root / f"f{i}.txt").write_text(str(i))
        home = str(Path(self.tmp.name) / ".home")
        out = self._pair("commit-boundary-gate.py", lambda r: {"cwd": str(r)}, extra_env={"HOME": home})
        self.assertIn("미커밋 변경", context(out["harness"]))
        self.assertIsNone(out["plain"])

    def test_session_summary_status_is_t1_but_commit_notice_is_t0(self):
        for root in (self.harness, self.plain):
            (root / "package.json").write_text("{}")
        home = Path(self.tmp.name) / ".home2"
        home.mkdir()
        out = self._pair("session-start-summary.py", lambda r: {"cwd": str(r)}, extra_env={"HOME": str(home)})
        self.assertIn("브랜치:", context(out["harness"]))
        self.assertNotIn("브랜치:", context(out["plain"]))
        self.assertIn("Bash(git commit *)", context(out["plain"]))

    def test_t0_hooks_still_fire_outside_jurisdiction(self):
        env = {"CLAUDE_PROJECT_DIR": str(self.plain)}
        self.assertTrue(denied(run_hook("block-destructive-git.py", {"tool_name": "Bash", "cwd": str(self.plain),
                                        "tool_input": {"command": "git reset --hard HEAD~1"}}, env=env)))
        self.assertTrue(denied(run_hook("gate-engine.py", {"tool_name": "Bash", "cwd": str(self.plain),
                                        "tool_input": {"command": ADD_UI_LIB}}, env=env)))

    def test_subagent_audit_records_verdicts_everywhere_but_diffs_only_in_jurisdiction(self):
        for root in (self.harness, self.plain):
            _git(str(root), "init", "-q", "-b", "main")
            (root / "src").mkdir()
            (root / "src" / "a.ts").write_text("a\n")
            _git(str(root), "add", "-A")
            _git(str(root), "commit", "-qm", "init")
        res = {}
        for k, root in (("harness", self.harness), ("plain", self.plain)):
            env = {"CLAUDE_PROJECT_DIR": str(root)}
            base = {"agent_id": "g1", "agent_type": "general-purpose", "session_id": "s", "cwd": str(root),
                    "stop_hook_active": False, "last_assistant_message": ""}
            run_hook("subagent-audit.py", dict(base, hook_event_name="SubagentStart"), env=env, cwd=str(root))
            (root / "src" / "a.ts").write_text("changed\n")
            res[k] = run_hook("subagent-audit.py", dict(base, hook_event_name="SubagentStop"), env=env, cwd=str(root))
            run_hook("subagent-audit.py", dict(base, hook_event_name="SubagentStop", agent_id="v1",
                     agent_type="react-supabase-harness:verifier", last_assistant_message="## Verdict\nFAIL"),
                     env=env, cwd=str(root))
            rec = (root / ".claude" / "state" / "verdicts.jsonl").read_text()
            self.assertIn('"FAIL"', rec, k)
        self.assertTrue(blocked(res["harness"]))
        self.assertIsNone(res["plain"])

    def test_harness_repo_itself_gets_only_the_listed_hooks(self):
        self.assertTrue(self.scope.applies("block-impl-delegation", ROOT))
        self.assertTrue(self.scope.applies("commit-boundary-gate", ROOT))
        self.assertFalse(self.scope.applies("edit-counter", ROOT))
        self.assertFalse(self.scope.applies("require-agent-output-file", ROOT))
        env = {"CLAUDE_PROJECT_DIR": str(ROOT)}

        def write(rel):
            return run_hook("block-impl-delegation.py", {"tool_name": "Write", "agent_id": "a1", "cwd": str(ROOT),
                            "agent_type": "general-purpose", "tool_input": {"file_path": str(ROOT / rel), "content": "x"}},
                            env=env)
        self.assertTrue(denied(write("hooks/new.py")))
        self.assertTrue(denied(write("tests/test_new.py")))
        self.assertIsNone(write("docs/notes.md"))


# ─────────────────────────────────────────────────────────────────────────────
# rm 정책(hooks/rmpolicy.py) — 정적 가드와 실행 시점 가드가 같은 대상을 같은 이유로 막는다
# ─────────────────────────────────────────────────────────────────────────────
def _rmpolicy():
    spec = importlib.util.spec_from_file_location("rmpolicy", HOOKS / "rmpolicy.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RmPolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(os.path.realpath(self.tmp.name))
        self.home = base / "home"
        self.home.mkdir()
        self.box = base / "box" / "deep"                         # 깊은 경로 — 시스템·홈 판정에 걸리지 않게
        self.proj = self.box / "proj"
        self.sib = self.box / "sibling"
        for r in (self.proj, self.sib):
            r.mkdir(parents=True)
            _git(str(r), "init", "-q", "-b", "main")
            Path(r, "a.txt").write_text("a\n")
            _git(str(r), "add", "-A")
            _git(str(r), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        self.rec = base / "rec.sh"
        self.rec.write_text("#!/bin/sh\nexit 0\n")
        self.rec.chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def static(self, cmd, cwd, project=None):
        return run_hook("block-destructive-git.py", {"tool_name": "Bash", "cwd": str(cwd), "tool_input": {"command": cmd}},
                        env={"CLAUDE_PROJECT_DIR": str(project or self.proj), "HOME": str(self.home)}, cwd=str(cwd))

    def runtime(self, args, cwd, project=None):
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.home), "HARNESS_REAL_RM": str(self.rec),
               "HARNESS_PROJECT_DIR": str(project or self.proj)}
        return subprocess.run([sys.executable, str(ROOT / "bin" / "rm"), *args], cwd=str(cwd), env=env,
                              capture_output=True, text=True)

    def both(self, target, cwd, project=None):
        """(정적 차단 여부, 실행 시점 차단 여부)"""
        st = denied(self.static(f"rm -rf {target}", cwd, project))
        rt = self.runtime(["-rf", target], cwd, project).returncode == 1
        return st, rt

    def test_session_project_git_dir_is_protected_in_both_layers(self):
        for target, cwd in [(".git", self.proj), ("./.git/", self.proj), (str(self.proj / ".git"), self.sib),
                            ("../proj/.git", self.sib)]:
            with self.subTest(target=target, cwd=cwd.name):
                self.assertEqual(self.both(target, cwd), (True, True))

    def test_other_repos_git_dir_is_not_protected(self):
        # D3 — 세션 프로젝트의 저장소만 지킨다. 다른 레포의 .git 은 막지 않는다(README 에 적힌 한계)
        for target, cwd in [(".git", self.sib), (str(self.sib / ".git"), self.proj)]:
            with self.subTest(target=target, cwd=cwd.name):
                self.assertEqual(self.both(target, cwd), (False, False))

    def test_project_and_its_parents_are_protected_from_anywhere(self):
        for target in (str(self.proj), str(self.box)):
            with self.subTest(target=target):
                self.assertEqual(self.both(target, self.sib), (True, True))
        self.assertEqual(self.both(str(self.proj / "dist"), self.sib), (False, False))   # 프로젝트 안 정리는 일상

    def test_monorepo_subproject_protects_the_enclosing_repo(self):
        sub = self.proj / "apps" / "web"
        sub.mkdir(parents=True)
        self.assertEqual(self.both(str(self.proj / ".git"), self.sib, project=sub), (True, True))

    def test_worktree_protects_its_git_file_and_the_common_dir(self):
        wt = self.box / "wt"
        _git(str(self.proj), "worktree", "add", "-q", str(wt), "-b", "w")
        for target in (str(wt / ".git"), str(self.proj / ".git"), str(self.proj / ".git" / "worktrees" / "wt")):
            with self.subTest(target=target):
                self.assertEqual(self.both(target, self.sib, project=wt), (True, True))

    def test_block_messages_carry_the_same_next_action(self):
        out = self.static("rm -rf .git", self.proj)
        r = self.runtime(["-rf", ".git"], self.proj)
        for text in (reason(out), r.stderr):
            self.assertIn("다음 행동", text)
            self.assertIn("git 저장소", text)

    def test_aliases_of_the_same_target_are_caught_by_identity(self):
        # 0.39.0 검증(High 회귀): macOS 는 대소문자를 무시하고 realpath 도 그것을 모으지 않아 문자열 비교가 뚫렸다
        probe = self.proj / "CaseProbe"
        probe.mkdir()
        if not (self.proj / "caseprobe").exists():
            self.skipTest("대소문자를 구분하는 파일시스템")
        up = Path(str(self.proj).replace("/proj", "/PROJ"))
        for target, cwd in [(str(up / ".git"), self.sib), (".GIT", self.proj), (str(self.proj / ".Git"), self.sib),
                            (str(up), self.sib)]:
            with self.subTest(target=target):
                self.assertEqual(self.both(target, cwd), (True, True))

    def test_git_dir_protection_does_not_depend_on_git(self):
        # 0.39.0 검증(Medium 회귀): git 호출이 실패하면(git 없음·GIT_DIR·타임아웃) 보호가 열렸다 — 이제 git 을 부르지 않는다
        env = {"PATH": "/nonexistent", "HOME": str(self.home), "HARNESS_REAL_RM": str(self.rec),
               "HARNESS_PROJECT_DIR": str(self.proj), "GIT_DIR": str(self.sib / ".git")}
        r = subprocess.run([sys.executable, str(ROOT / "bin" / "rm"), "-rf", ".git"], cwd=str(self.proj), env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stderr)
        del env["HARNESS_PROJECT_DIR"]                      # 세션 프로젝트를 모르면 지금 위치의 저장소를 지킨다
        r = subprocess.run([sys.executable, str(ROOT / "bin" / "rm"), "-rf", ".git"], cwd=str(self.proj), env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1, r.stderr)

    def test_containers_and_insides_of_the_git_dir_are_protected(self):
        wt = self.box / "wt2"
        _git(str(self.proj), "worktree", "add", "-q", str(wt), "-b", "w2")
        # 워크트리 세션에서 메인 체크아웃(공용 .git 을 품음)을 지우면 저장소가 사라진다
        self.assertEqual(self.both(str(self.proj), self.sib, project=wt), (True, True))
        self.assertEqual(self.both(str(self.proj / ".git" / "objects"), self.sib), (True, True))
        sep = self.box / "sepwork"
        store = self.box / "store"
        store.mkdir()
        _git(str(self.box), "init", "-q", "--separate-git-dir", str(store / "s.git"), str(sep))
        self.assertEqual(self.both(str(store), self.sib, project=sep), (True, True))

    def test_static_layer_resolves_home_user_and_globs(self):
        home_proj = self.home / "Projects" / "p"
        home_proj.mkdir(parents=True)
        _git(str(home_proj), "init", "-q")
        for cmd in ["rm -rf $HOME/Projects/p", "rm -rf ${HOME}/Projects/p/.git", "rm -rf .gi?", "rm -rf ./.g*",
                    f"rm -rf {home_proj}"]:
            with self.subTest(cmd=cmd):
                self.assertTrue(denied(self.static(cmd, home_proj, project=home_proj)))
        user = os.path.basename(os.path.expanduser("~"))
        self.assertTrue(denied(self.static(f"rm -rf ~{user}", self.sib)))
        sub = self.proj / "src"
        sub.mkdir()
        self.assertTrue(denied(self.static(f"rm -rf {sub}", sub)))           # cwd 자신을 절대 경로로
        self.assertIsNone(self.static("rm -rf .gi?", self.sib))              # 형제 레포는 D3 대로 통과

    def test_static_glob_expansion_is_capped(self):
        # 0.39.0 재검증(N1): 글롭 펼침에 상한이 없어 깊은 글롭 하나로 훅이 수십 초 걸렸다
        many = self.box / "many"
        many.mkdir()
        for i in range(2005):
            (many / f"f{i}").write_text("")
        out = self.static(f"rm -rf {many}/*", self.sib)
        self.assertTrue(denied(out))
        self.assertIn("판정할 수 없다", reason(out))
        self.assertIsNone(self.static(f"rm -rf {many}/f1*", self.sib))           # 상한 안은 그대로 판정

    def test_system_tops_are_measured_by_identity_too(self):
        pol = _rmpolicy()
        if not os.path.isdir("/System/Volumes/Data/opt"):
            self.skipTest("macOS 데이터 볼륨이 없다")
        for p in ("/System/Volumes/Data", "/System/Volumes/Data/opt", "/System/Volumes/Data/usr/local"):
            with self.subTest(p=p):
                self.assertEqual((pol.why(p) or ("",))[0], "system")
        self.assertEqual((pol.why("/private/var/folders") or ("",))[0], "system")
        self.assertIsNone(pol.why(str(self.box / "x")))

    def test_escaped_braces_in_recursive_rm_are_not_guessed(self):
        # 차분 퍼즈(시드 4242): 토크나이저가 `\{` 의 백슬래시를 지워 `{,\{/../}~` 를 bash(→ `~`)와 다르게 펼쳤다(0.38.0 부터)
        out = self.static("rm -rf \\\n{,\\{/../}~", self.sib)
        self.assertTrue(denied(out))
        self.assertIn("다음 행동", reason(out))
        self.assertIsNone(self.static("rm -rf build/{a,b}", self.sib))          # 이스케이프 없는 중괄호는 그대로 판정

    def test_wrapper_smoke(self):
        self.assertEqual(self.runtime(["-r", str(self.box / "tmpx")], self.sib).returncode, 0)
        self.assertEqual(self.runtime(["-r", str(self.home)], self.sib).returncode, 1)
        self.assertEqual(self.runtime(["notes.txt"], self.proj).returncode, 0)        # 재귀가 아니면 판정 없음
