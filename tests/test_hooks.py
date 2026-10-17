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

# 설치된 하네스의 실행 시점 가드(<플러그인>/bin/git·rm)가 PATH 에 있는 세션에서 돌려도 결과가 같아야 한다 —
# 픽스처 저장소(임시 디렉터리)의 커밋까지 막혀 HEAD 가 없어지면 판정이 달라진다(0.43.0 설치 직후 실측).
# 픽스처는 사용자 저장소가 아니다. 래퍼 자체는 GitWrapperTest 가 bin/ 을 직접 PATH 에 넣어 검사한다.
os.environ["PATH"] = os.pathsep.join(
    d for d in os.environ.get("PATH", "").split(os.pathsep)
    if not (d and Path(d).name == "bin" and (Path(d).parent / "hooks" / "rmpolicy.py").is_file()))

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

    def test_heredoc_delimiter_is_read_like_bash(self):
        # 0.41.0 자체 점검(0.40.1 부터): 종료어를 이름 부분만 잡아 `<<EOF"X"`(실제 종료어 EOFX)·`<<E$X`(글자 그대로)의
        # 뒤 명령을 본문으로 숨겼다. 파괴 가드도 shellparse 의 표식·종료어 판정을 쓴다
        for c in ['cat <<EOF"X"\nEOFX\ngit push --force\nEOF', 'cat <<E$X\nE$X\ngit push --force\nE',
                  'X=foo; cat <<E$X\nE$X\ngit push --force\nEfoo', 'cat <<\\EOF\nEOF\ngit reset --hard\nx']:
            with self.subTest(c=c):
                self.assertTrue(denied(self.check(c)))
        # 데이터 본문은 여전히 명령이 아니다(종료 줄이 정확히 일치할 때)
        for c in ["cat > notes.md <<'EOF'\n(git push --force 금지)\nEOF", 'cat > a <<"E O F"\ngit reset --hard\nE O F']:
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

    def test_branch_force_delete_in_bundled_short_flags(self):
        # 0.42.4 까지 `-D`·`-d` 토큰 일치만 봐서 묶음이 통과했다(git 차분 퍼즈 — 런처·bash -c·heredoc 모든 감싸기에서)
        for c in ["git branch -Df x", "git branch -fd x", "git branch -df x", "git branch -dr --force x",
                  "bash -c 'git branch -df x'", "git -C . branch -fd x"]:
            with self.subTest(c=c):
                self.assertTrue(denied(self.check(c)))
        for c in ["git branch -d x", "git branch -dv x", "git branch -vv", "git branch -m old new", "git branch -a"]:
            with self.subTest(c=c):
                self.assertIsNone(self.check(c))

    def test_git_alias_cannot_shadow_a_builtin(self):
        # git 은 내장 명령과 같은 이름의 별칭을 무시한다 — 0.43.0 초안은 펼쳐서 진짜 push·reset 을 통과시켰다(push 전 보안 리뷰)
        for c in ["git -c alias.push=status push --force origin main", "git -c alias.reset=status reset --hard",
                  "git -c alias.branch=log branch -D x", "git -c 'alias.x=!git push --force' x",
                  "git -c 'alias.p=push \"--force\"' p origin main", "git reset --har", "git clean --forc",
                  "git -c remote.origin.push=+HEAD:main push", "git -c clean.requireForce=false clean -d",
                  "git config remote.origin.push +HEAD:main", "git config clean.requireForce false",
                  "git config alias.x '!git push -f'", "git config --global core.hooksPath /dev/null"]:
            with self.subTest(c=c):
                self.assertTrue(denied(self.check(c)))
        for c in ["git -c alias.st=status st", "git push --force-with-lease origin main", "git restore --stag f",
                  "git config user.name x", "git config alias.st status", "git config --get remote.origin.url",
                  "git config --get remote.origin.push", "git config get clean.requireForce"]:
            with self.subTest(c=c):
                self.assertIsNone(self.check(c))

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
        self.assertFalse(any(x.startswith("grep") for x in
                             gate.read_only_shapes("Bash", {"command": 'grep "a\\"b" x.txt > out.txt; git status'})))
        # 5차 독립 검증: 함수 재정의·zsh 프로세스 치환·`$''(` — 원문 허용 문자 밖이면 어느 하위 명령도 읽기 전용이 아니다
        for cmd in ["head() { touch b; }; head", "sort =(touch b)", "ls $''(touch x); git status", "ls *(e:touch x:)",
                    # 6차: 빈 따옴표 인자, 같은 호출 안의 재바인딩, 셸이 공백으로 보지 않는 문자 뒤의 리다이렉트
                    "git config user.name ''; git status", "PATH=/tmp/x; head a", "hash -p /bin/rm head; head v"]:
            with self.subTest(direct=cmd):
                self.assertEqual(gate.read_only_shapes("Bash", {"command": cmd}), set())
        # 셸이 공백으로 보지 않는 문자 뒤의 리다이렉트는 그 하위 명령만 자격을 잃는다(0.41.0 — 하위 명령 층)
        self.assertNotIn("head in", gate.read_only_shapes("Bash", {"command": "head in >\v/dev/null; ls"}))
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

    def test_read_only_tail_survives_unrelated_shell_syntax_in_the_same_call(self):
        # 0.41.0 S4 — 실사용 오탐(2026-10-12 세션 재생 head 9·tail 7): 같은 호출의 **다른** 하위 명령에 `$S`·heredoc·
        # `>>`·대입이 있다는 이유로 `| head`·`| tail`·`; git status` 까지 신원으로 남아 매 턴 거짓 정정을 요구했다
        for chain, later in [("cd $S && python3 x.py 2>&1 | tail -5", "tail -5"),
                             ("cat >> tests/t.py <<'PYEOF'\nx = 1\nPYEOF\ngit status --short | head -3", "git status --short"),
                             ("S=/tmp/x && rm -rf $S && ls src | head", "head"),
                             ("python3 - <<'EOF'\nprint(open('a').read())\nEOF\ngit log --oneline | head -5", "git log --oneline"),
                             # 호출 층 거부 원인 실측 상위(빈 인자 124·치환 대입 156·출력 printf 35건 / 다중 하위 명령 1,520건)
                             ("sed -i '' 's/a/b/' f.txt && git status --short | head -3", "head -3"),
                             ("T=$(mktemp -d) && cp a $T && ls | head", "head"),
                             ("printf '%s\\n' a b > list.txt; wc -l list.txt | tail -1", "tail -1"),
                             ("git ls-files | while read f; do touch $f; done; git status --short", "git status --short")]:
            with self.subTest(chain=chain):
                lines = [user_text("a"), tool_use("a", chain),
                         tool_result("a", True, "PreToolUse:Bash hook error: 차단"), say("차단됐습니다."),
                         user_text("b"), tool_use("b", later), tool_result("b", False), say("확인했습니다.")]
                self.assertIsNone(self.stop(lines))

    def test_call_level_rebinding_voids_every_read_only_segment(self):
        # 호출 층 — 같은 호출 안에서 뒤 하위 명령의 이름·출력·환경을 바꾸는 구조가 있으면 어느 것도 읽기 전용이 아니다
        gate = load_module("report-failures-gate.py")
        for cmd in ["X=1 head a", "LC_ALL=C sort a", "for PATH in /x; do head a; done", "{ head a; } > o; ls",
                    "( head a ) > o; ls", "exec > o; head a", "read PATH; head a", ": ${PATH:=/x}; head a",
                    "printf -v PATH x; head a", "trap 'touch b' EXIT; head a", '"eval" "head(){ :;}"; head a',
                    "setopt noclobber; head a", "source ./x; head a", ". ./x; head a", "alias head=rm; head a",
                    "GIT_EXTERNAL_DIFF=/tmp/x; git diff", "path=/tmp; head a", "f() { :; }; head a",
                    "unset -f head; head a", "builtin cd /; head a", "export PATH; head a",
                    "read -a PATH; head a", "while read PATH; do head a; done", "printf -v PATH x; ls",
                    "X=$(a) PATH=/tmp head a", "GIT_DIR=/tmp/x && git status",
                    # 0.41.0 독립 검증 1회차: 첫 낱말 앞에 대입·리다이렉트·수식어를 붙인 재바인딩, 값으로 만든 printf 옵션
                    "x=1 export PATH=/tmp; head a", ">/dev/null export PATH=/x; head a", "2>&1 export PATH=/x; head a",
                    "command export PATH=/x; head a", "time -p export PATH=/x; head a",
                    "case x in x) export PATH=/x;; esac; head a", "noglob export PATH=/x; head a",
                    "repeat 1 export PATH=/x; head a", "- export PATH=/x; head a", "O=-v; printf $O PATH x; head a",
                    "{export,} PATH=/x; head a", 'ex""port PATH=/x; head a',
                    # 0.40.1 부터: 덧붙이는 대입·배열 원소 대입
                    "PATH+=:/tmp/evil; head a", "path[1]=/tmp/evil; head a"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(gate.read_only_shapes("Bash", {"command": cmd}), set())
        # 안전한 이름의 문장 단위 대입은 다른 하위 명령의 자격을 지우지 않는다
        self.assertIn("head", gate.read_only_shapes("Bash", {"command": "S=/tmp/x; touch $S/a; ls | head"}))
        # 빈 인자가 있으면 인자 수를 보는 명령만 자격을 잃는다(`git config user.name ''` 는 쓰기)
        ro = gate.read_only_shapes("Bash", {"command": "git config user.name ''; uniq a.txt; ls | head"})
        self.assertIn("head", ro)
        self.assertFalse(any(x.startswith(("git", "uniq")) for x in ro))

    def test_shared_segment_layer_forms(self):
        # 0.41.0 S4 — 스냅샷 재생(실제 3,038건)에서 새로 찍히던 상위 형태를 공용 층에 넣고, 같은 머리의 쓰기 변형은 막는다
        sp = load_module("shellparse.py")
        ro = lambda c: [sp.segment_read_only(g, sp.call_level(c)) for g, _ in sp.segments(c, "/tmp", shell_only=True)]
        for cmd in ["sed -n 1,60p src/a.ts", "sed -n 12p a", "find src -name '*.md' -type f", "printf '%s\\n' a",
                    "cat < in.txt", "cat <<EOF\nx\nEOF", "echo 완료", "grep -n x ~/a/*.json", 'grep "$BASE" f',
                    "git -C sub log --oneline", "git grep -n foo", "gh pr diff 3", "wc -l <<< abc"]:
            with self.subTest(read=cmd):
                self.assertTrue(all(ro(cmd)), cmd)
        for cmd in ["sed -i 1d a", "sed -n 1w out a", "sed -n 1e a", "sed 1,2p a", "sed -n 1p a > b",
                    "find . -delete", "find . -execdir rm {} +", "find . -fprint0 out", "find . -okdir rm {} ;",
                    "printf -v X a", "cat <> f", "cat < <(touch x)", "echo $(touch x)", "echo `touch x`",
                    "git -c core.pager=x log", "git grep -Ovi foo", "git grep --open-files-in-pager=vi x",
                    "head a > b", "./head a", "echo a \\\"; touch b",
                    # 0.41.0 독립 검증 1회차: git grep -O 묶음·축약, find 의 변수 인자
                    "git grep --op=rm x", "git grep --open-file=rm x", "git grep -iOrm x", "find . $X"]:
            with self.subTest(write=cmd):
                self.assertFalse(all(ro(cmd)), cmd)

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

    def test_nul_byte_does_not_crash_the_gate_open(self):
        # NUL 뒤는 bash 에 닿지 않는데, 판정 쪽 경로 함수는 ValueError 로 죽어 술어가 빠졌다(0.42.4 까지 fail-open — 예외 퍼즈)
        out = self.gate("Bash", {"command": f"cat > src/new.ts <<'EOF'\n{self.BIG}\nEOF\n&>>\0"})
        self.assertTrue(denied(out))

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


class PlanFirstOutcomeTest(_RepoCase):
    """plan-first 의 보장 층 — 명령 글자 대신 명령이 실제로 만들거나 바꾼 파일로 판정한다(실행 전 stat 기준 ↔ 실행 뒤)."""
    BIG = "\n".join(f"export const v{i} = {i}" for i in range(40))

    def run_cmd(self, write, tid="toolu_1", command="python3 gen.py"):
        ti = {"command": command}
        pre = self.hook("gate-engine.py", {"tool_name": "Bash", "tool_input": ti, "hook_event_name": "PreToolUse",
                                           "tool_use_id": tid})
        write()
        post = self.hook("gate-engine.py", {"tool_name": "Bash", "tool_input": ti, "hook_event_name": "PostToolUse",
                                            "tool_use_id": tid})
        return pre, post

    def test_code_written_by_any_means_is_caught_after_the_fact(self):
        # 정적 판정이 읽지 못하는 쓰기 — 인터프리터·스크립트·생성기. 실행 전엔 통과하고 실행 뒤 결과로 걸린다
        for i, write in enumerate([lambda: Path(self.proj, "src", "gen.ts").write_text(self.BIG),
                                   lambda: Path(self.proj, "src", "old.ts").write_text(self.BIG),
                                   lambda: (Path(self.proj, "supabase", "migrations").mkdir(exist_ok=True),
                                            Path(self.proj, "supabase", "migrations", "1_a.sql").write_text("x"))]):
            with self.subTest(i=i):
                pre, post = self.run_cmd(write, tid=f"toolu_{i}")
                self.assertIsNone(pre)
                self.assertTrue(blocked(post), post)
                self.assertIn("다음 행동", post["reason"])
                self.assertIn("/react-supabase-harness:phase", post["reason"])
                _git(self.proj, "add", "-A")
                _git(self.proj, "commit", "-qm", f"step{i}")

    def test_small_change_untouched_files_and_non_code_pass(self):
        Path(self.proj, "src", "dirty.ts").write_text(self.BIG)          # 이 명령 전부터 있던 변경은 이 명령 탓이 아니다
        for i, write in enumerate([lambda: Path(self.proj, "src", "old.ts").write_text("y\n"),
                                   lambda: Path(self.proj, "notes.md").write_text(self.BIG),
                                   lambda: None]):
            with self.subTest(i=i):
                pre, post = self.run_cmd(write, tid=f"toolu_s{i}")
                self.assertIsNone(post)

    def test_plan_or_hatch_means_nothing_is_measured(self):
        Path(self.proj, "docs", "plans").mkdir()
        Path(self.proj, "docs", "plans", "phase-1.md").write_text("# plan")
        _, post = self.run_cmd(lambda: Path(self.proj, "src", "gen.ts").write_text(self.BIG))
        self.assertIsNone(post)

    def test_failed_command_is_judged_too(self):
        # 종료 코드 ≠ 0 은 PostToolUse 가 아니라 PostToolUseFailure 로 온다 — 거기엔 block 이 없어 오류 옆에 붙인다(push 전 리뷰 F2)
        ti = {"command": "python3 gen.py && false"}
        self.hook("gate-engine.py", {"tool_name": "Bash", "tool_input": ti, "hook_event_name": "PreToolUse",
                                     "tool_use_id": "toolu_f"})
        Path(self.proj, "src", "gen.ts").write_text(self.BIG)
        out = self.hook("gate-engine.py", {"tool_name": "Bash", "tool_input": ti, "hook_event_name": "PostToolUseFailure",
                                           "tool_use_id": "toolu_f"})
        ctx = out["hookSpecificOutput"]
        self.assertEqual(ctx["hookEventName"], "PostToolUseFailure")
        self.assertIn("다음 행동", ctx["additionalContext"])

    def test_size_is_measured_against_the_state_before_the_command(self):
        # HEAD 가 아니라 실행 전 내용 — 앞선 변경이 쌓인 파일의 한 줄 수정, 미추적 파일의 한 줄 수정, HEAD 로의 되돌림은
        # 이 명령의 큰 변경이 아니다(push 전 리뷰 F1·F1b: 되돌리라는 안내를 따른 명령까지 막혔다)
        old, u = Path(self.proj, "src", "old.ts"), Path(self.proj, "src", "u.ts")
        old.write_text(self.BIG)
        u.write_text(self.BIG)
        for i, write in enumerate([lambda: old.write_text(self.BIG + "\nexport const z = 1"),
                                   lambda: u.write_text(self.BIG.replace("v0 ", "w0 ")),
                                   lambda: old.write_text("x\n")]):
            with self.subTest(i=i):
                _, post = self.run_cmd(write, tid=f"toolu_m{i}")
                self.assertIsNone(post)
        # 줄을 제자리에서 고친 것은 한 번만 센다(-1 +1 = 1줄) — 15줄 수정은 Edit 처럼 통과한다(2차 리뷰 F4)
        lines = self.BIG.split("\n")
        _, post = self.run_cmd(lambda: u.write_text("\n".join(x + " // c" if i < 15 else x for i, x in enumerate(lines))),
                               tid="toolu_m8")
        self.assertIsNone(post)
        # 지우기만 한 것은 Edit(빈 new_string)처럼 작은 변경이다(3차 리뷰)
        _, post = self.run_cmd(lambda: u.write_text("\n".join(lines[:5])), tid="toolu_m7")
        self.assertIsNone(post)
        u.write_text(self.BIG)
        # 실행 전 내용과 비교해 큰 변경은 그대로 걸린다
        _, post = self.run_cmd(lambda: u.write_text("\n".join(f"let q{i} = {i}" for i in range(40))), tid="toolu_m9")
        self.assertTrue(blocked(post))

    def test_background_commands_are_refused_while_measuring(self):
        # 실행 뒤 훅이 돌 때 백그라운드 명령은 아직 쓰지 않았을 수 있다 — 잴 수 없으면 실행 전에 막는다(push 전 리뷰 F3)
        for ti in ({"command": "node scripts/codegen.mjs", "run_in_background": True},
                   {"command": "node scripts/codegen.mjs &"}, {"command": "(node gen.mjs &) ; echo"}):
            with self.subTest(ti=ti):
                out = self.gate("Bash", ti)
                self.assertTrue(denied(out))
                self.assertIn("다음 행동", reason(out))
        for ti in ({"command": "coproc python3 gen.py"}, {"command": "setsid -f python3 gen.py"},
                   {"command": "tmux new-session -d 'python3 gen.py'"}, {"command": "python3 gen.py & disown"}):
            with self.subTest(ti=ti):
                self.assertTrue(denied(self.gate("Bash", ti)))
        # 2차 리뷰 F2 — 셸의 백그라운드가 아닌 `&`
        for ti in ({"command": "npm run build 2>&1 | tail"}, {"command": "true && ls &>/dev/null"},
                   {"command": "echo 'a & b'"}, {"command": "python3 - <<'EOF'\nm = (a > 1) & (b < 2)\nx &= 3\nEOF"},
                   {"command": "cat <&0"}, {"command": "echo $(( 5 & 3 ))"}, {"command": "echo a\\&b"},
                   {"command": "tmux ls"}, {"command": 'git commit -m "feat: x\n\nat least one test added"'},
                   {"command": "python3 -c \"\nbatch = [1]\nprint(batch)\""}):
            with self.subTest(ti=ti):
                self.assertIsNone(self.gate("Bash", ti))
        Path(self.proj, "docs", "plans").mkdir()
        Path(self.proj, "docs", "plans", "phase-1.md").write_text("# plan")
        self.assertIsNone(self.gate("Bash", {"command": "node scripts/codegen.mjs &"}))

    def test_record_lives_in_the_project_state_and_refuses_links(self):
        # 공용 /tmp 의 예측 가능한 경로는 다른 사용자가 미리 만들어 둘 수 있었다(정리 루프가 남의 파일을 지움, push 전 리뷰 F4)
        self.run_cmd(lambda: None, tid="toolu_loc")
        state = Path(self.proj, ".claude", "state")
        self.assertTrue((state / "gate-pre").is_dir())
        self.assertEqual(_git(self.proj, "status", "--porcelain").stdout, "")   # 기록은 사용자 저장소를 더럽히지 않는다
        victim = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(victim, ignore_errors=True))
        old = victim / "keep.json"
        old.write_text("{}")
        os.utime(old, (1, 1))
        shutil.rmtree(state / "gate-pre")
        (state / "gate-pre").symlink_to(victim)
        _, post = self.run_cmd(lambda: Path(self.proj, "src", "gen.ts").write_text(self.BIG), tid="toolu_link")
        self.assertTrue(old.exists())
        self.assertEqual(sorted(x.name for x in victim.iterdir()), ["keep.json"])

    def test_restoring_head_content_is_not_new_code(self):
        # 잘려 있던 추적 파일을 HEAD 로 되살리면 많은 줄이 다시 들어온다 — 이미 커밋된 내용이라 새 코드가 아니다(pull·브랜치 전환도 같다)
        big = Path(self.proj, "src", "big.ts")
        big.write_text(self.BIG)
        _git(self.proj, "add", "-A")
        _git(self.proj, "commit", "-qm", "big")
        big.write_text("\n".join(self.BIG.split("\n")[:3]))
        _, post = self.run_cmd(lambda: big.write_text(self.BIG), tid="toolu_r1")
        self.assertIsNone(post)
        # 지워졌던 추적 파일을 되살리는 것도 같다(실행 전엔 없던 파일 = 새 파일 판정을 받지 않는다)
        big.unlink()
        _, post = self.run_cmd(lambda: big.write_text(self.BIG), tid="toolu_r2")
        self.assertIsNone(post)

    def test_revert_to_head_passes_in_a_subfolder_project(self):
        # `git -C <프로젝트> show HEAD:<경로>` 는 저장소 루트 기준이라 하위 폴더 프로젝트에선 늘 실패했다(2차 리뷰 F1) — `HEAD:./`
        sub = Path(self.proj, "app")
        (sub / "src").mkdir(parents=True)
        (sub / ".harness.json").write_text('{"role": "project"}')
        (sub / "src" / "a.ts").write_text("x\n")
        _git(self.proj, "add", "-A")
        _git(self.proj, "commit", "-qm", "app")
        (sub / "src" / "a.ts").write_text(self.BIG)
        def hook(event):
            return run_hook("gate-engine.py", {"tool_name": "Bash", "tool_input": {"command": "x"}, "cwd": str(sub),
                                               "hook_event_name": event, "tool_use_id": "toolu_sub"},
                            env={"CLAUDE_PROJECT_DIR": str(sub)}, cwd=str(sub))
        hook("PreToolUse")
        (sub / "src" / "a.ts").write_text("x\n")                  # 안내대로 HEAD 로 되돌린 명령
        self.assertIsNone(hook("PostToolUse"))

    def test_no_pre_record_means_no_judgment(self):
        Path(self.proj, "src", "gen.ts").write_text(self.BIG)
        post = self.hook("gate-engine.py", {"tool_name": "Bash", "tool_input": {"command": "x"},
                                            "hook_event_name": "PostToolUse", "tool_use_id": "toolu_none"})
        self.assertIsNone(post)


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

    def test_snapshot_uses_the_shared_read_only_layers(self):
        # 0.41.0 S4 — 스냅샷의 '찍지 않아도 되는 명령' 판정은 정정 게이트와 같은 공용 두 층이다. 머리 이름만 보던
        # 목록(env·open·jq·awk·gh api·git fetch)은 이제 찍는다. 레포 밖으로만 쓰는 리다이렉트는 여전히 건너뛴다
        Path(self.proj, "src", "wip.ts").write_text("uncommitted\n")
        for cmd in ["cat src/wip.ts | head", "echo done", "git status --short 2>/dev/null | head -3",
                    "grep -rn x src > /tmp/o", "find src -name x", "sed -n 1p src/wip.ts"]:
            with self.subTest(skip=cmd):
                self.snap(cmd)
                self.assertEqual(self.snapshots(), [])
        for cmd in ["env rm -rf src/x", "find src -delete", "jq . a.json > src/a.json", "head() { :; }; head",
                    "PATH=/tmp/x; ls", "sed -i 1d src/wip.ts", "echo x > src/new.ts", "awk 1 src/wip.ts",
                    "git fetch origin",
                    # 0.41.0 독립 검증 1회차(0.40.1 부터의 구멍): 명령 낱말 없는 리다이렉트, heredoc 종료어 오인
                    ">src/wip.ts", "exec >src/wip.ts", "command exec >src/wip.ts",
                    'cat <<EOF"X"\nEOFX\ntouch src/pwned\nEOF',
                    # 자체 점검: 종료어의 `$` 는 펼치지 않는다(bash 는 글자 그대로)
                    'cat <<E$X\nE$X\ntouch src/pwned\nE', 'X=foo; cat <<E$X\nE$X\ntouch src/pwned\nEfoo',
                    'X=foo\ncat <<"$X"\n$X\ntouch src/pwned\nfoo']:
            with self.subTest(snap=cmd):
                snap = load_module("snapshot-guard.py")
                self.assertTrue(snap._repos(cmd, self.proj, os.path.realpath(self.proj)))

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

    def test_other_version_wrapper_on_path_does_not_loop(self):
        # 플러그인 업데이트 뒤 PATH 에 옛 버전 bin 이 남으면 두 래퍼가 서로를 execv 해 rm 이 끝나지 않았다(0.36.0–0.42.0).
        # 옛 래퍼는 표지 검사가 없으므로 그쪽이 다시 이 래퍼를 골라도 HARNESS_RM_WRAPPED 가드로 멈춰야 한다.
        with tempfile.TemporaryDirectory() as d:
            home = os.path.realpath(d)
            old = Path(home, "old-bin")
            old.mkdir()
            shutil.copy(ROOT / "bin" / "rm", old / "rm")      # 다른 디렉터리의 같은 래퍼 = 다른 버전
            text = (old / "rm").read_text()
            (old / "rm").write_text(text.replace("and not _is_wrapper(p)", "and True")
                                        .replace('if os.environ.get("HARNESS_RM_WRAPPED")', "if False"))  # 옛 버전 흉내
            (old / "rm").chmod(0o755)
            Path(home, "victim").write_text("x")
            for first, second in ((ROOT / "bin", old), (old, ROOT / "bin")):
                with self.subTest(first=first.name):
                    Path(home, "victim").write_text("x")
                    env = {"PATH": f"{first}:{second}:/usr/bin:/bin", "HOME": home, "HARNESS_PROJECT_DIR": home}
                    p = subprocess.run(["bash", "-c", "rm -f victim"], capture_output=True, text=True, env=env,
                                       cwd=home, timeout=20)
                    # 옛 래퍼가 앞이어도 그것이 고르는 다음 rm 이 새 래퍼이므로 새 래퍼에서 끊긴다
                    self.assertEqual(p.returncode, 0, p.stderr)
                    self.assertFalse(Path(home, "victim").exists())


# ─────────────────────────────────────────────────────────────────────────────
# 실행 시점 git 가드(bin/git, 0.43.0) — 정적 가드와 같은 정책(hooks/gitpolicy.py)을 bash 가 해석을 끝낸 실제 인자에 건다
# ─────────────────────────────────────────────────────────────────────────────
GIT_REC = """#!/bin/sh
# 기록용 진짜 git — 설정 조회(config --get/--get-regexp)에는 테스트가 정한 값을 답한다. 래퍼의 설정 조회는 전역 옵션을
# 앞에 붙이므로(-C . config …) 그 한 쌍을 건너뛰고 본다
A1=$1; A2=$2; A3=$3
if [ "$1" = "-C" ] || [ "$1" = "-c" ]; then A1=$3; A2=$4; A3=$5; fi
if [ "$A1" = "config" ] && [ "$A2" = "--get" ]; then
  case "$A3" in alias.pf) echo "push --force"; exit 0;; alias.co) echo "commit -m x"; exit 0;;
               alias.sh) echo "!git status"; exit 0;;
               clean.requireForce) [ -n "$REQ" ] && { echo "$REQ"; exit 0; };; esac
  exit 1
fi
if [ "$A1" = "config" ] && [ "$A2" = "--get-regexp" ]; then
  [ -n "$RPUSH" ] && { echo "$RPUSH"; exit 0; }
  exit 1
fi
if [ "$3" = "rev-parse" ]; then echo abc123; exit 0; fi
for a in "$@"; do printf '%s\\037' "$a" >> "$__OUT"; done
printf '\\036' >> "$__OUT"
"""


class GitWrapperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(os.path.realpath(self.tmp.name))
        self.rec = self.home / "real-git"
        self.rec.write_text(GIT_REC)
        self.rec.chmod(0o755)
        self.out = self.home / "out.bin"

    def tearDown(self):
        self.tmp.cleanup()

    def run_git(self, args, extra=None, shell=None):
        if self.out.exists():
            self.out.unlink()
        env = {"PATH": f"{ROOT / 'bin'}:/usr/bin:/bin", "HOME": str(self.home), "HARNESS_REAL_GIT": str(self.rec),
               "__OUT": str(self.out), "HARNESS_PROJECT_DIR": str(self.home), **(extra or {})}
        cmd = [sys.executable, str(ROOT / "bin" / "git"), *args] if shell is None else ["bash", "-c", shell]
        return subprocess.run(cmd, cwd=str(self.home), env=env, capture_output=True, text=True, timeout=20)

    def reached(self) -> list:
        if not self.out.exists():
            return []
        return [c.split("\x1f")[:-1] for c in self.out.read_text().split("\x1e") if c]

    def test_destructive_real_args_are_blocked_with_next_action(self):
        for args in (["push", "--force"], ["push", "origin", "+main"], ["reset", "--hard"], ["branch", "-Df", "x"],
                     ["clean", "-fd"], ["commit", "-m", "x"], ["-C", ".", "push", "-f"], ["checkout", "--", "."]):
            with self.subTest(args=args):
                p = self.run_git(args)
                self.assertEqual(p.returncode, 1, p.stderr)
                self.assertIn("다음 행동", p.stderr)
                self.assertEqual(self.reached(), [])

    def test_routine_git_reaches_the_real_git_unchanged(self):
        for args in (["status"], ["push", "--force-with-lease", "origin", "main"], ["branch", "-d", "x"],
                     ["reset", "--soft", "HEAD~1"], ["log", "--oneline", "-3"]):
            with self.subTest(args=args):
                p = self.run_git(args)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(self.reached(), [args])

    def test_what_the_static_guard_cannot_see_is_seen_here(self):
        # 정적 가드는 글자를 추측한다 — 변수·$(…)·eval·배열·xargs·별칭은 여기서는 이미 풀린 인자다
        for sh in ("F=--force; git push $F", "git $(echo push) $(echo -f)", "eval \"git push -f\"",
                   "A=(reset --hard); git \"${A[@]}\"", "echo -D x | xargs git branch",
                   "git -c alias.zz='push --force' zz", "git pf"):
            with self.subTest(sh=sh):
                p = self.run_git([], shell=sh)
                self.assertNotEqual(p.returncode, 0, p.stderr)
                self.assertEqual(self.reached(), [])

    def test_approved_commit_passes_unless_verify_failed(self):
        p = self.run_git(["commit", "-m", "x"], {"CLAUDE_COMMIT_APPROVED": "1"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.reached(), [["commit", "-m", "x"]])
        # 별칭으로 부른 커밋도 커밋이다
        self.assertEqual(self.run_git(["co"]).returncode, 1)
        # 훅 기록이 지금 HEAD 위의 FAIL 이면 승인 커밋도 막는다. HEAD 가 바뀌었으면(그 뒤 커밋) 판단하지 않는다
        state = self.home / ".claude" / "state"
        state.mkdir(parents=True)
        for head, code in (("abc123", 1), ("old999", 0)):
            with self.subTest(head=head):
                (state / "verdicts.jsonl").write_text(json.dumps({"agent_type": "verifier", "verdict": "FAIL", "head": head}) + "\n")
                self.assertEqual(self.run_git(["commit", "-m", "x"], {"CLAUDE_COMMIT_APPROVED": "1"}).returncode, code)

    def test_shell_alias_is_blocked(self):
        # `!` 별칭 — git 은 셸을 띄우기 전에 자기 exec-path(진짜 git 이 있는 곳)를 PATH 앞에 둔다. 그 안의 git 은
        # 이 래퍼를 지나지 않으므로 여기서 막는다(0.43.0 초안은 "다시 지난다"고 가정하고 통과시켰다 — push 전 보안 리뷰)
        p = self.run_git(["sh"])
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertIn("다음 행동", p.stderr)
        self.assertEqual(self.reached(), [])

    def test_policy_follows_git_semantics_not_spelling(self):
        # 별칭은 git 과 같은 규칙으로 펼친다(내장 명령 우선·따옴표 분리), 긴 옵션은 git 처럼 접두어도 그 옵션이다
        for args in (["-c", "alias.push=status", "push", "--force", "origin", "main"],
                     ["-c", "alias.reset=status", "reset", "--hard"], ["-c", "alias.branch=log", "branch", "-D", "x"],
                     ["-c", "alias.x=!git push --force", "x"], ["-c", 'alias.p=push "--force"', "p"],
                     ["-c", "alias.p=push '-f'", "p"], ["reset", "--har"], ["clean", "--forc"],
                     ["push", "--delet", "origin", "main"], ["branch", "--delet", "--forc", "x"], ["Reset", "--hard"]):
            with self.subTest(args=args):
                self.assertEqual(self.run_git(args).returncode, 1)
                self.assertEqual(self.reached(), [])
        # 승인 표식은 실제 commit 에만 — 별칭 이름으로 다른 명령을 승인시키지 못한다
        p = self.run_git(["-c", "alias.push=commit", "push", "--force"], {"CLAUDE_COMMIT_APPROVED": "1"})
        self.assertEqual(p.returncode, 1, p.stderr)
        # 설정이 동작을 바꾸는 것 — 설정 refspec 의 강제 push, clean.requireForce=false 의 -f 없는 clean
        for args, env in ((["-c", "remote.origin.push=+HEAD:main", "push"], None),
                          (["-c", "clean.requireForce=false", "clean", "-d"], None), (["clean", "-d"], {"REQ": "false"}),
                          (["push"], {"RPUSH": "remote.origin.push +HEAD:main"}),
                          (["push", "origin"], {"RPUSH": "remote.origin.mirror true"}),
                          (["branch", "-M", "x", "main"], None), (["branch", "-fm", "x", "main"], None)):
            with self.subTest(args=args, env=env):
                self.assertEqual(self.run_git(args, env).returncode, 1)
                self.assertEqual(self.reached(), [])
        for args, env in ((["clean", "-n"], {"REQ": "false"}), (["clean", "-d"], None),
                          (["push"], {"RPUSH": "remote.origin.push refs/heads/main:refs/heads/main"}),
                          (["branch", "-m", "old", "new"], None)):
            with self.subTest(args=args, env=env):
                self.assertEqual(self.run_git(args, env).returncode, 0)
        for args in (["-c", "alias.st=status", "st"], ["restore", "--stag", "f"], ["push", "--force-with-lease"]):
            with self.subTest(args=args):
                self.assertEqual(self.run_git(args).returncode, 0)
                self.assertEqual(self.reached(), [args])

    def test_broken_policy_fails_closed_only_for_risky_subcommands(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d)
            (fake / "bin").mkdir()
            (fake / "hooks").mkdir()
            shutil.copy(ROOT / "bin" / "git", fake / "bin" / "git")
            (fake / "hooks" / "gitpolicy.py").write_text("raise ImportError('broken')\n")
            env = {"PATH": "/usr/bin:/bin", "HOME": str(self.home), "HARNESS_REAL_GIT": str(self.rec), "__OUT": str(self.out)}
            run = lambda *a: subprocess.run([sys.executable, str(fake / "bin" / "git"), *a], env=env, cwd=str(self.home),
                                            capture_output=True, text=True, timeout=20).returncode
            self.assertEqual(run("push", "-f"), 1)
            self.assertEqual(run("status"), 0)

    def test_other_version_wrapper_on_path_does_not_loop(self):
        # rm 래퍼의 0.36.0–0.42.0 무한 execv 를 git 에서 처음부터 막는다 — 다른 디렉터리의 래퍼는 진짜 git 이 아니다
        with tempfile.TemporaryDirectory() as d:
            other = Path(os.path.realpath(d), "other-bin")
            other.mkdir()
            shutil.copy(ROOT / "bin" / "git", other / "git")
            real = Path(os.path.realpath(d), "real")
            real.mkdir()
            shutil.copy(self.rec, real / "git")
            for first, second in ((ROOT / "bin", other), (other, ROOT / "bin")):
                with self.subTest(first=first.name):
                    if self.out.exists():
                        self.out.unlink()
                    env = {"PATH": f"{first}:{second}:{real}:/usr/bin:/bin", "HOME": str(self.home), "__OUT": str(self.out)}
                    p = subprocess.run(["bash", "-c", "git status"], capture_output=True, text=True, env=env,
                                       cwd=str(self.home), timeout=20)
                    self.assertEqual(p.returncode, 0, p.stderr)
                    self.assertEqual(self.reached(), [["status"]])


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


# ─────────────────────────────────────────────────────────────────────────────
# 셸 해석 단일화 (0.40.0 S3) — 런처·토크나이저·git 전역 옵션은 shellparse 한 곳, 위치를 모르는 cd 는 UNKNOWN
# ─────────────────────────────────────────────────────────────────────────────
class ShellParseUnifiedTest(_RepoCase):
    BIG = "\n".join(f"export const v{i} = {i}" for i in range(40))

    def setUp(self):
        super().setUp()
        self.sp = load_module("shellparse.py")

    def snap(self, cmd):
        return self.hook("snapshot-guard.py", {"tool_name": "Bash", "tool_input": {"command": cmd}})

    def snapshots(self):
        return _git(self.proj, "for-each-ref", "--format=%(objectname)", "refs/harness/snapshots/").stdout.split()

    def test_launchers_are_read_the_same_everywhere(self):
        for cmd, head in [("nice -n 10 rm src/x", ["rm", "src/x"]), ("env -u X gh pr create", ["gh", "pr", "create"]),
                          ("timeout -s KILL 5 rm a", ["rm", "a"]), ("sudo -u bob /bin/rm a", ["rm", "a"]),
                          ("xargs -I {} cp {} src/", ["cp", "{}", "src/"]), ("then time command rm a", ["rm", "a"])]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.sp.tokens(cmd), head)
        self.assertEqual(self.sp.git_sub(["git", "-C", "a", "--namespace", "n", "-c", "k=v", "push"]), (7, ["a"]))
        self.assertEqual(self.sp.git_sub(["git", "--version"])[0], -1)

    def test_command_regex_sees_through_valued_launcher_options(self):
        ge = load_module("gate-engine.py")
        for c in ["env -u X gh pr create --fill", "if gh pr create; then :; fi", "/usr/bin/gh pr create"]:
            with self.subTest(c=c):
                self.assertTrue(ge.p_command_regex({"tool": "Bash", "command": c, "cwd": "."}, r"gh\s+pr\s+create")[0])
        self.assertFalse(ge.p_command_regex({"tool": "Bash", "command": "echo 'gh pr create'", "cwd": "."},
                                            r"gh\s+pr\s+create")[0])

    def test_cd_to_an_unknown_place_snapshots_the_session_project(self):
        Path(self.proj, "src", "wip.ts").write_text("uncommitted\n")
        self.snap("cd $HOME/Projects/x && rm -rf zz")
        self.assertEqual(len(self.snapshots()), 1)
        Path(self.proj, "src", "wip2.ts").write_text("more\n")
        self.snap('cd "$S/proj" && rm -rf zz')
        self.assertEqual(len(self.snapshots()), 2)
        self.assertIsNone(self.snap("cd $X && ls && cat a"))           # 읽기만 하면 찍지 않는다
        self.assertEqual(len(self.snapshots()), 2)
        segs = self.sp.segments("cd - && rm a; cd /tmp && rm b; cd c && rm d; popd; rm e", "/p")
        self.assertEqual([h for _, h in segs], [self.sp.UNKNOWN, "/tmp", "/tmp/c", self.sp.UNKNOWN])

    def test_lossy_notice_follows_the_program_not_the_text(self):
        Path(self.proj, "src", "wip.ts").write_text("uncommitted\n")
        self.assertIsNone(self.snap("grep -rn rm src > /dev/null && touch src/n.ts"))   # 찍되 알리지 않는다
        self.assertEqual(len(self.snapshots()), 1)
        Path(self.proj, "src", "wip3.ts").write_text("x\n")
        self.assertIn("git show", context(self.snap("sudo rm src/old.ts")))

    def test_interpreter_heredoc_strings_are_not_shell_writes(self):
        mark_harness(self.proj)
        py = "python3 - <<'EOF'\nimport os\nos.system(\"rm -rf src\")\nprint('x > src/a.ts')\nEOF"
        out = run_hook("block-impl-delegation.py", {"tool_name": "Bash", "tool_input": {"command": py},
                                                    "agent_type": "general-purpose", "agent_id": "a1", "cwd": self.proj},
                       env={"CLAUDE_PROJECT_DIR": self.proj})
        self.assertIsNone(out)
        self.assertIsNone(self.gate("Bash", {"command": py}))
        # 셸이 읽는 heredoc 은 그대로 명령이다
        sh = f"bash <<'EOF'\ncat > src/new.ts <<'X'\n{self.BIG}\nX\nEOF"
        self.assertTrue(denied(self.gate("Bash", {"command": sh})))

    def test_unknown_cwd_relative_target_is_judged_not_skipped(self):
        # 0.40.0 첫 구현은 위치 모르는 상대 경로를 건너뛰어 `cd "$X" && …` 한 줄로 plan-first·위임 차단을 껐다(커밋 보안
        # 리뷰 — 0.39.0 대비 회귀). plan-first 는 모르는 대상으로, 위임 차단은 시작 위치 기준으로 본다
        mark_harness(self.proj)
        for c in [f"cd $X && cat > new.ts <<'EOF'\n{self.BIG}\nEOF", f"cd - && cat > new.ts <<'EOF'\n{self.BIG}\nEOF",
                  f"cd $X && cat > {self.proj}/src/new.ts <<'EOF'\n{self.BIG}\nEOF"]:
            with self.subTest(c=c[:30]):
                self.assertTrue(denied(self.gate("Bash", {"command": c})))
        for c in ['cd "$X" && cat > src/a.ts', "popd; rm -rf src/a", "pushd +1 && cp x src/a.ts", 'git -C "$X" checkout develop']:
            with self.subTest(c=c):
                out = run_hook("block-impl-delegation.py", {"tool_name": "Bash", "agent_type": "general-purpose", "agent_id": "a1",
                                                            "cwd": self.proj, "tool_input": {"command": c}},
                               env={"CLAUDE_PROJECT_DIR": self.proj})
                self.assertTrue(denied(out))

    def test_source_counts_only_at_command_position(self):
        la = self.sp.leading_assignments
        self.assertEqual(la("S=/tmp/x && git add . && rm -rf $S/y"), {"S": "/tmp/x"})
        self.assertEqual(la("S=/tmp/x && echo source && rm $S"), {"S": "/tmp/x"})
        for c in ["S=/a && . ./e && rm $S", 'S=/a; "." e; rm $S', "S=/a; \\source e; rm $S",
                  "S=/a; if true; then . e; fi; rm $S", "S=/a; >/dev/null . e; rm $S", "S=/a; { . e; }; rm $S",
                  "S=/a; command . e; rm $S", "S=/a; X=1 . e; rm $S"]:
            with self.subTest(c=c):
                self.assertEqual(la(c), {})

    def test_source_through_redirects_options_and_variables_bails(self):
        # 독립 검증 1회차 F1 — 명령 위치 열거는 리다이렉트·옵션·변수 경유를 놓쳤다
        la = self.sp.leading_assignments
        for c in ["S=/a; 2>&1 . e; rm $S", "S=/a; >&2 . e; rm $S", "S=/a; >| /dev/null . e; rm $S", "S=/a; time -p . e",
                  "S=/a; command -p . e", "S=/a; command -- . e", "S=/a; X=source; $X e", "S=/a; echo x # it's\n. e",
                  "S=/a; X='.'; $X e", "S=/a; /dev/null . e"]:
            with self.subTest(c=c):
                self.assertEqual(la(c), {})
        self.assertEqual(la("S=/tmp/x && cp ${S} . && ls ."), {"S": "/tmp/x"})

    def test_quoted_newlines_and_loose_heredoc_ends_do_not_hide_commands(self):
        # 독립 검증 1회차 F2 — 따옴표 짝 오판과 `EOF ` 종료 줄
        seg = lambda c: [x for x, _ in self.sp.segments(c, "/p")]
        self.assertIn("cp x src/a.ts", seg("echo \"$(echo '\"')\"\ncp x src/a.ts"))
        self.assertIn("cp x src/a.ts", seg("cat <<EOF\nEOF \nit's\nEOF\ncp x src/a.ts"))

    def test_heredoc_inside_quoted_command_substitution_is_data(self):
        # 커밋·PR 본문의 정형 — 큰따옴표 안 `$(cat <<'EOF' …)` 의 본문은 데이터다(첫 수정이 본문을 명령으로 읽었다)
        c = "gh pr create --title t --body \"$(cat <<'EOF'\n## 왜\nit's broken > src/x.ts\nEOF\n)\" && ls"
        self.assertEqual([x for x, _ in self.sp.segments(c, "/p")][1:], ["ls"])
        self.assertIsNone(self.snap(c))

    def test_env_split_string_value_is_the_command(self):
        # 독립 검증 1회차 F3 — `env -S` 의 값은 실행된다
        for c, t in [("env -S 'cp x' src/a.ts", ["cp", "x", "src/a.ts"]), ("env -S 'tee src/a.ts'", ["tee", "src/a.ts"]),
                     ("env --split-string='cp x' y", ["cp", "x", "y"])]:
            with self.subTest(c=c):
                self.assertEqual(self.sp.tokens(self.sp.segments(c, "/p")[0][0]), t)
        bdg = load_module("block-destructive-git.py")
        self.assertTrue(bdg.scan("env -S 'git push --force'"))

    def test_bare_env_split_string_does_not_crash_the_hooks(self):
        # 독립 검증 2회차 N1 — 값 없는 `env -S` 가 IndexError 로 훅을 죽였다(비차단 오류 = fail-open)
        self.assertEqual(self.sp.strip_launchers(["env", "-S"]), [])
        mark_harness(self.proj)
        for c in ["env -S && cp x src/a.ts", "env -S; touch .claude/state/plan-gate-off"]:
            with self.subTest(c=c):
                out = self.gate("Bash", {"command": c})
                self.assertTrue(denied(out))
        out = run_hook("block-impl-delegation.py", {"tool_name": "Bash", "agent_type": "general-purpose", "agent_id": "a1",
                                                    "cwd": self.proj, "tool_input": {"command": "env -S && cp x src/a.ts"}},
                       env={"CLAUDE_PROJECT_DIR": self.proj})
        self.assertTrue(denied(out))

    def test_indirect_execution_of_a_dot_argument_bails(self):
        # 독립 검증 2회차 N2 — 인자 `.` 가 함수·위치 매개변수·변수 머리로 나중에 실행된다
        la = self.sp.leading_assignments
        for c in ['S=/a; f(){ "$@"; }; f . ./s.sh; rm -rf $S', 'S=/a; set -- . ./s.sh; "$@"', "S=/a; set -- source; $1 e",
                  "S=/a; f(){ $1 $2; }; f source e", 'S=/a; cp x .; "$@"', "S=/a; ls .;$1 e"]:
            with self.subTest(c=c):
                self.assertEqual(la(c), {})
        self.assertEqual(la('S=/tmp/x && git add . && rm -rf "$S"/y'), {"S": "/tmp/x"})

    def test_brace_expanded_source_and_variable_heads_bail(self):
        # 독립 검증 3회차 R3-1(회귀) — `{source,}` 는 중괄호 확장으로 source 가 된다
        # R3-2 — 명령 낱말이 변수·치환이면 무엇이 실행될지 모른다(`$X`·`$(printf '\56')`), alias 는 나중 줄을 바꾼다
        la = self.sp.leading_assignments
        for c in ["S=/a; {source,} ./s.sh; rm -rf $S", "S=/a; {.,} ./s.sh", "S=/a; $X ./s.sh",
                  "S=/a; $(printf '\\56') ./s.sh", "S=/a; `printf .` ./s.sh", "S=/a; alias s=.\ns ./s.sh"]:
            with self.subTest(c=c):
                self.assertEqual(la(c), {})
        for c, want in [("S=/tmp/x && git add . && rm $S", {"S": "/tmp/x"}), ("S=/tmp/x; cp ${S} . && ls", {"S": "/tmp/x"}),
                        ("S=/tmp/x; echo {a,b}; rm $S", {"S": "/tmp/x"})]:
            with self.subTest(c=c):
                self.assertEqual(la(c), want)

    def test_transformed_known_variable_keeps_its_value(self):
        # 0.40.0 퍼즈(시드 31337) — `${S/x}` 를 모르는 변수 하나로 접어 `S=/; rm -rf ${S/x}usr` 가 `usr` 로 읽혔다(0.39.0 까지)
        bdg = load_module("block-destructive-git.py")
        # 그 수정만 쓰면 접두를 깎는 변형이 깊은 경로로 읽혔다(독립 검증 4회차 R4-1, 회귀 High) — 두 후보를 다 본다
        for c in ["S=/; rm -rf ${S/x}usr", "S=/; rm -rf ${S#x}usr", "export S=/\nrm -r -f ${S/...\\057}}",
                  "rm -rf ${HOME%/*}", "rm -rf ${PWD%/*/*}", "S=/tmp/hx/a; rm -rf ${S%/hx/a}", "S=/tmp/hx/a; rm -rf ${S:0:4}",
                  "S=/tmp/hx/a; rm -rf ${S//?/}/usr", "S=/tmp/hx/a; rm -rf ${S#/tmp/hx/a}/usr", "S=/tmp/hx/a; rm -rf /${S:99}"]:
            with self.subTest(c=c):
                self.assertTrue(bdg.scan(c))
        self.assertTrue(bdg.scan("S=x/..; rm -rf ${S/x}usr"))                        # 치환이 앞에 `/` 를 만든다(`/..usr`)
        self.assertTrue(bdg.scan("S=/tmp/a; rm -rf ${S/x}b"))                         # 대가: 안전한 변형도 막는다(드묾)
        for c in ["rm -rf ${PWD::1}usr", "rm -rf ${HOME%/*}x", "f(){ S=/; }; f; rm -rf ${S/x}usr",
                  "S=/tmp/hx/a; r=S; rm -rf ${!r%/*}", "A=(/ x); rm -rf ${A[0]/x}usr"]:     # 환경 변수·함수·간접 참조·배열(자체 점검)
            with self.subTest(c=c):
                self.assertTrue(bdg.scan(c))
        self.assertIsNone(bdg.scan("rm -rf ./${X%.ts}.bak"))                          # 상대 경로 앞 변형은 그대로 통과
        self.assertIsNone(bdg.scan("for f in a.ts; do rm -f ${f%.ts}.js; done"))

    def test_glob_command_name_may_be_source(self):
        # 독립 검증 4회차 R4-2 — `sourc?`·`[s]ource` 는 cwd 의 파일 `source` 로 펼쳐져 builtin source 가 된다
        la = self.sp.leading_assignments
        for c in ["S=/a; sourc? ./s.sh; rm $S", "S=/a; [s]ource ./s.sh", "S=/a; s*ce ./s.sh"]:
            with self.subTest(c=c):
                self.assertEqual(la(c), {})
        self.assertEqual(la("S=/a; ls *.ts; rm $S"), {"S": "/a"})
        self.assertEqual(la('R=/a; if [ "$N" != 2 ]; then exit; fi; [[ -n x ]]; git -C $R push'), {"R": "/a"})  # 시험 명령은 글롭 아님

    def test_escaped_space_before_hash_is_not_a_comment(self):
        # 독립 검증 2회차 N3 — `a\ #b` 의 `#` 는 낱말 안이다
        self.assertEqual(self.sp.leading_assignments("S=/a; echo a\\ # ; . ./s.sh\nrm -rf $S"), {})
        self.assertIn("rm -rf src", [x for x, _ in self.sp.segments("echo a\\ #b; rm -rf src", "/p")])
        # 파괴 가드의 자체 normalize 도 같은 구멍이었다(0.39.0 까지 — `\ #` 뒤 명령 전체가 검사 밖)
        bdg = load_module("block-destructive-git.py")
        self.assertTrue(bdg.scan("echo a\\ # ; git push --force origin main"))
        self.assertIsNone(bdg.scan("echo hi # git push --force"))

    def test_cd_pwd_is_the_current_place(self):
        # 독립 검증 1회차 F4 — `cd "$PWD"` 로 위치 판정을 끄지 못한다
        self.assertEqual(self.sp.segments('cd "$PWD" && cat > src/a.ts', "/p")[0][1], "/p")
        self.assertEqual(self.sp.segments("cd ${PWD}/sub && rm a", "/p")[0][1], "/p/sub")

    def test_escaped_double_quote_does_not_split_the_command(self):
        segs = self.sp.segments('echo "a \\" ; rm -rf src" && ls', "/p")
        self.assertEqual(len(segs), 2)
        self.assertTrue(segs[0][0].startswith("echo"))

    def test_data_strings_and_comments_do_not_hide_later_commands(self):
        seg = lambda c: [x for x, _ in self.sp.segments(c, "/p")]
        self.assertIn("rm -rf src", seg('gh pr create --body "fix #12" && rm -rf src'))    # 문자열 속 `#` 는 주석이 아니다
        self.assertIn("rm -rf src", seg('echo "a <<EOF"\nrm -rf src\nEOF'))              # 문자열 속 `<<EOF` 는 heredoc 이 아니다
        self.assertEqual(seg("# it's a comment\nrm -rf src"), ["rm -rf src"])            # 주석 속 `'` 는 따옴표가 아니다
        self.assertEqual(seg("cat > f <<EOF\nrm -rf src\nEOF\nls"), ["cat > f <<EOF", "ls"])
        mark_harness(self.proj)
        out = run_hook("block-impl-delegation.py", {"tool_name": "Bash", "agent_type": "general-purpose", "agent_id": "a1",
                                                    "cwd": self.proj, "tool_input": {"command": 'gh pr create --body "fix #1" && rm -rf src'}},
                       env={"CLAUDE_PROJECT_DIR": self.proj})
        self.assertTrue(denied(out))

    def test_pushd_popd_return_to_a_known_place(self):
        segs = self.sp.segments("pushd src && rm a.ts && popd; rm b; popd; rm c", "/p")
        self.assertEqual([h for _, h in segs], ["/p/src", "/p", self.sp.UNKNOWN])

    def test_prose_in_a_data_heredoc_is_not_source(self):
        c = "S=/a && cat > $S/x.md <<'EOF'\n(x). 기간\nsource 확인\n. 끝\nEOF"
        self.assertEqual(self.sp.leading_assignments(c), {"S": "/a"})

    def test_report_failures_shapes_use_the_shared_reading(self):
        rf = load_module("report-failures-gate.py")
        shape = lambda c: rf.cmd_shapes("Bash", {"command": c})
        self.assertEqual(shape("nice -n 10 rm -rf build"), ["rm -fr build"])
        self.assertEqual(shape("git -C x --namespace n push origin"), ["git push origin"])
        self.assertEqual(shape("find . | xargs rm"), ["find ."])            # xargs 는 신원 없는 실행기 그대로
        self.assertEqual(shape("for d in a; do rm $d; done"), ["rm $d"])


class ShellParseCompletenessTest(unittest.TestCase):
    """셸 해석 표는 shellparse 한 곳에만 있다 — 상수 이름이 아니라 AST 로 본다(이름을 바꿔 다시 만들어도 걸린다)."""
    LAUNCHER_NAMES = {"sudo", "doas", "nohup", "env", "nice", "stdbuf", "xargs", "exec", "builtin", "command", "timeout"}
    GIT_GLOBALS = {"--git-dir", "--work-tree", "--namespace"}

    def test_no_hook_keeps_its_own_launcher_or_git_option_table(self):
        import ast
        bad = []
        files = [p for p in HOOKS.glob("*.py") if p.name != "shellparse.py"] + [ROOT / "bin" / "rm", ROOT / "bin" / "git"]
        for f in files:
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Set, ast.Tuple, ast.List, ast.Dict)):
                    elts = node.keys if isinstance(node, ast.Dict) else node.elts
                    strs = {e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
                    if len(strs & self.LAUNCHER_NAMES) >= 2 or len(strs & self.GIT_GLOBALS) >= 2:
                        bad.append(f"{f.name}:{node.lineno}")
                # 하위 명령 문자열을 공백으로 다시 쪼개지 않는다 — shellparse.words/tokens 를 쓴다
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "split"
                        and not node.args and isinstance(node.func.value, ast.Name)
                        and node.func.value.id in ("seg", "segment", "sub")):
                    bad.append(f"{f.name}:{node.lineno} .split()")
        self.assertEqual(bad, [])

    def test_no_hook_keeps_its_own_read_only_table(self):
        # 0.41.0 S4 — 읽기 전용 판정은 shellparse.call_admissible·segment_read_only 한 곳이다
        import ast
        heads = {"head", "tail", "grep", "wc", "cat", "ls", "stat", "status", "rev-parse", "ls-files"}
        bad = []
        for f in [p for p in HOOKS.glob("*.py") if p.name != "shellparse.py"]:
            for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
                    strs = {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
                    if len(strs & heads) >= 3:
                        bad.append(f"{f.name}:{node.lineno}")
        self.assertEqual(bad, [])
