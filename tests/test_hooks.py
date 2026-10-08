"""훅 계약 테스트 — 각 훅을 Claude Code 가 부르는 그대로(stdin JSON → stdout JSON) 실행한다.

왜 블랙박스인가: 이 하네스의 주장은 "게이트는 결정론적이다"이다. 내부 함수가 아니라
**훅 계약**(무엇을 넣으면 deny/block/알림/침묵 중 무엇이 나오나)을 고정해야 그 주장이 검증된다.

실행:  python3 -m unittest discover -s tests -v      (의존성 없음 — 표준 라이브러리만)
"""
from __future__ import annotations
import importlib.util
import json
import os
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


def run_hook(name: str, payload, env: dict | None = None, cwd: str | None = None) -> dict | None:
    """훅을 실행하고 stdout JSON 을 돌려준다. 출력이 없으면 None(= 통과·침묵)."""
    e = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_", "HARNESS_GATE_"))}
    e["CLAUDE_PLUGIN_ROOT"] = str(ROOT)
    e.update(env or {})
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
        # 깊은 경로 정리는 일상이다 / 변수만 있는 경로·.git·-f 없는 -r
        for c in ["rm -rf /tmp/build-x", "rm -rf /Users/me/proj/dist", "rm -rf ~/.cache/tool/x"]:
            with self.subTest(c=c):
                self.assertIsNone(self.check(c))
        for c in ['rm -rf "$DIR"/', "rm -rf $PWD", "rm -rf .git", "rm -r ~", "rm -rf ~/Projects",
                  "rm -rf /Users/me"]:
            with self.subTest(c=c):
                self.assertTrue(denied(self.check(c)))

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

    def test_ordinary_failure_then_other_target_is_not_a_reversal(self):
        # A38: 판단이 아니라 상황인 실패(없음·네트워크) 뒤의 다른 대상 성공에 거짓 정정을 요구했다
        for first, err, second in [("rm -rf dist", "No such file or directory", "rm -rf node_modules"),
                                   ("git push origin feat-a", "Could not resolve host", "git push origin feat-b"),
                                   ("rm -rf dist", "No such file or directory", "rm -rf dist")]:
            with self.subTest(first=first, second=second):
                lines = [user_text("a"), tool_use("a", first), tool_result("a", True, err), say("실패했습니다."),
                         user_text("b"), tool_use("b", second), tool_result("b", False), say("됐습니다.")]
                self.assertIsNone(self.stop(lines))

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
        (self.repo / ".gitignore").write_text(".claude/\n")

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
        self.proj = Path(self.tmp.name)

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
        unparseable = "echo $'it\\'s'"
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
            self.assertNotIn("Bash(git commit *)", context(run_hook("session-start-summary.py", {}, env=env, cwd=d)))


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
