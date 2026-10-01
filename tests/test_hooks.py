"""훅 계약 테스트 — 각 훅을 Claude Code 가 부르는 그대로(stdin JSON → stdout JSON) 실행한다.

왜 블랙박스인가: 이 하네스의 주장은 "게이트는 결정론적이다"이다. 내부 함수가 아니라
**훅 계약**(무엇을 넣으면 deny/block/알림/침묵 중 무엇이 나오나)을 고정해야 그 주장이 검증된다.

실행:  python3 -m unittest discover -s tests -v      (의존성 없음 — 표준 라이브러리만)
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


class StripJsoncTest(unittest.TestCase):
    """2026-08-17 실측 버그 회귀: 정규식으로 주석을 벗기면 문자열 안 glob 이 지워졌다."""

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
                  "rm -fr /tmp/x", "rm -rf ~/cache", "rm -r -f ../up", "git commit -m x", "git -C . commit -m x",
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

    def test_long_agent_without_output_file_denied(self):
        self.assertTrue(denied(self.agent("require-agent-output-file.py", "general-purpose", "시장 조사해줘")))
        self.assertIsNone(self.agent("require-agent-output-file.py", "general-purpose", "조사해서 /tmp/r.md 에 저장"))
        self.assertIsNone(self.agent("require-agent-output-file.py", "Explore", "찾아줘"))


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
        self.assertIn("systemMessage", out)

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

    def stop(self):
        return run_hook("commit-boundary-gate.py", {"cwd": str(self.repo)})

    def test_below_band_is_silent(self):
        self.files(5)
        self.assertIsNone(self.stop())

    def test_band_notifies_once(self):
        self.files(25)
        out = self.stop()
        self.assertIn("systemMessage", out)
        self.assertFalse(blocked(out))  # 알림만 — 차단하지 않는다
        self.assertIsNone(self.stop())  # 같은 띠에서 반복 금지

    def test_next_band_notifies_again(self):
        self.files(25)
        self.stop()
        self.files(45)
        self.assertIn("40+", self.stop()["systemMessage"])

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
        for t in ["감사합니다", "리뷰한거야?", "proposal-review 스킬 설명"]:
            with self.subTest(t=t):
                self.assertIsNone(self.prompt(t))


# ─────────────────────────────────────────────────────────────────────────────
# 매니페스트 — hooks.json 이 가리키는 파일이 실재하고 버전이 일치한다
# ─────────────────────────────────────────────────────────────────────────────
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


if __name__ == "__main__":
    unittest.main()
