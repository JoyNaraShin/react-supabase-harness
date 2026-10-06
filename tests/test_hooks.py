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
        self.assertEqual({r["id"] for r in rules}, {"no-ui-library", "plan-first"})


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

    def test_implementation_delegation_denied(self):
        self.assertTrue(denied(self.agent("block-impl-delegation.py", "general-purpose",
                                          "src/features/cart/api.ts 파일을 구현해라. 결과는 /tmp/o.md")))

    def test_reviewer_delegation_passes(self):
        self.assertIsNone(self.agent("block-impl-delegation.py", "craft-reviewer",
                                     "src/features/cart/api.ts 리뷰. 결과는 /tmp/o.md"))

    def test_user_approved_bypass(self):
        self.assertIsNone(self.agent("block-impl-delegation.py", "general-purpose",
                                     "[HARNESS: 구현위임 승인됨] src/a.ts 를 구현해라."))

    def test_delegation_corpus(self):
        # 미탐과 오탐을 한 코퍼스로 고정
        hook = "block-impl-delegation.py"
        for t, p in [("general-purpose", "src/a.ts 의 버그를 수정해줘"),
                     ("general-purpose", "src/a.ts 에 검증 로직을 추가해줘"),
                     ("general-purpose", "fix the bug in src/lib/format.ts"),
                     ("general-purpose", "update src/App.tsx to use the new layout"),
                     ("my-research-impl", "src/a.ts 를 구현해라")]:
            with self.subTest(t=t, p=p):
                self.assertTrue(denied(self.agent(hook, t, p)))
        for t, p in [("react-supabase-harness:planner", "plan 작성해. 대상 파일 docs/plans/phase-1.md, src/ 참고"),
                     ("general-purpose", "레포를 감사하라. 파일 수정 금지. 결과를 /tmp/a/report.md 에 작성하라. hooks/x.py 를 읽어라"),
                     ("general-purpose", "Audit hooks/x.py. Write your report to /tmp/r.md. Do not modify files."),
                     ("general-purpose", "파일 쓰기·편집 툴(Write/Edit)이 src/ 에 닿는지 조사"),
                     ("Plan", "src/ 구조를 바꿀 계획을 세워라")]:
            with self.subTest(t=t, p=p):
                self.assertIsNone(self.agent(hook, t, p))
        send = lambda to, m: run_hook(hook, {"tool_name": "SendMessage", "tool_input": {"to": to, "message": m}})
        self.assertIsNone(send("main", "src/a.ts 를 수정해야 할 것 같습니다 — 보고"))
        self.assertTrue(denied(send("code-reviewer", "src/a.ts 를 구현해라")))  # 수신자 이름은 타입 근거가 아니다

    def test_resuming_an_agent_with_implementation_work_is_denied(self):
        # SendMessage 재개는 새 스폰과 같은 위임이다
        out = run_hook("block-impl-delegation.py", {"tool_name": "SendMessage", "tool_input": {
            "to": "helper", "message": "이어서 src/features/cart/api.ts 를 구현해라."}})
        self.assertTrue(denied(out))
        self.assertIsNone(run_hook("block-impl-delegation.py", {"tool_name": "SendMessage", "tool_input": {
            "to": "helper", "message": "진행 상황만 알려줘."}}))

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
        lines = [user_text("a"), tool_use("a", "vercel ls"), tool_result("a", True), say("실패했습니다."),
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
        lines = [user_text("a"), tool_use("a", "git push origin main"), tool_result("a", True, "rejected"),
                 say("push 가 거부돼 실패했습니다."),
                 user_text("b"), tool_use("b", "git push origin main"), tool_result("b", False), say("올렸습니다.")]
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

    def test_delegation_verbs_and_noise(self):
        def spawn(p):
            return run_hook("block-impl-delegation.py", {"tool_name": "Agent", "tool_input": {
                "prompt": p, "subagent_type": "general-purpose"}})
        for p, want in [("src/auth.ts 에 로그인 로직을 구현할 것", True),
                        ("src/auth.ts 를 다음 스펙대로 구현하시오", True),
                        ("이 패치를 src/a.ts 에 적용해줘", True),
                        ("src/components/Button.tsx 를 생성해", True),
                        ("Rewrite src/a.ts to use hooks", True),
                        ("결과 화면 컴포넌트 src/Result.tsx 를 작성해", True),
                        ("Do not modify tests, just implement src/a.ts", True),
                        ("Review the change in src/a.ts and verify the fix. 결과를 /tmp/r.md 에 저장", False),
                        ("src/a.ts 를 읽고 고쳐야 할 점을 나열하라. 결과를 /tmp/r.md 에 저장", False),
                        ("list what to fix in src/a.ts; save your report to /tmp/r.md", False)]:
            with self.subTest(p=p):
                self.assertEqual(denied(spawn(p)), want)


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


if __name__ == "__main__":
    unittest.main()
