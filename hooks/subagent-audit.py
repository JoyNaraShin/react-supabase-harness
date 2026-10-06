#!/usr/bin/env python3
"""SubagentStart / SubagentStop hook — 서브에이전트 실행 전후를 결과로 감사한다.

① 코드 영역 사후 diff (RULES §7 보강)
   block-impl-delegation 은 서브에이전트의 Write·Edit·Bash 쓰기 대상을 실행 전에 막는다. 그런데 포매터
   (`prettier --write`)·생성기·패키지 설치·스크립트는 명령 문자열에 대상이 드러나지 않는다. 그래서
   시작할 때 작업 트리 스냅샷(hooks/gitsnap.py)을 찍고, 끝날 때 코드 영역에 바뀐 경로가 있으면 한 번
   멈춰 세워(SubagentStop block) 최종 보고에 그 목록을 싣게 한다. 되돌리지는 않는다 — 같은 시간에
   사용자가 편집기로 고친 파일일 수 있고, 그 판단은 메인 세션과 사용자의 몫이다.

② 판정 기록 (자기 신고 보완)
   verifier 의 PASS/FAIL, 리뷰어의 VERDICT·Critical 수를 **훅이** `.claude/state/verdicts.jsonl` 에 적는다.
   이 파일은 gate-engine 이 에이전트 쓰기로부터 보호하므로, 기록은 대화 속 주장이 아니라 실제로 끝난
   서브에이전트의 마지막 메시지에서 나온다. block-destructive-git 이 승인 커밋 직전에 읽는다.

한계: 백그라운드 서브에이전트가 메인 세션과 동시에 돌면 그 사이 메인·사용자의 변경이 섞인다(목록에
"사용자·메인 편집일 수 있다"를 명시한다). git 레포가 아니면 ①은 건너뛴다. 기록은 에이전트 타입과
메시지 형식만 본다 — 판정의 품질은 검사하지 않는다.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gitsnap  # noqa: E402

VERDICT_FILE = Path(".claude") / "state" / "verdicts.jsonl"
HARNESS_AGENTS = {"verifier", "craft-reviewer", "stability-reviewer", "structure-fitness-reviewer",
                  "plan-consistency-reviewer", "planner"}
VERIFIER = re.compile(r"##\s*Verdict\s*\n+\s*(PASS|FAIL)\b")
VERDICT = re.compile(r"VERDICT\W{0,3}(FIT|GAP|REDESIGN|TIGHTEN|REBUILD)\b|결함 없음\s*\((FIT)\)")
CRITICAL = re.compile(r"Critical\D{0,6}(\d+)", re.IGNORECASE)


def _delegation():
    import importlib.util
    spec = importlib.util.spec_from_file_location("deleg", Path(__file__).with_name("block-impl-delegation.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _state_file(repo: str, agent_id: str):
    gd = gitsnap._git(repo, "rev-parse", "--absolute-git-dir")
    if not gd:
        return None
    d = Path(gd) / "harness-subagents"
    d.mkdir(exist_ok=True)
    return d / re.sub(r"[^\w.-]", "_", agent_id)


def parse_verdict(agent_type: str, text: str):
    name = agent_type.rsplit(":", 1)[-1]
    if name not in HARNESS_AGENTS:
        return None
    rec = {"agent_type": name}
    m = VERIFIER.search(text or "")
    if name == "verifier":
        rec["verdict"] = m.group(1) if m else "UNPARSED"
    else:
        v = VERDICT.search(text or "")
        rec["verdict"] = (v.group(1) or v.group(2)) if v else None
        c = CRITICAL.search(text or "")
        rec["critical"] = int(c.group(1)) if c else None
    return rec


def record_verdict(root: Path, data: dict, repo) -> None:
    rec = parse_verdict(str(data.get("agent_type") or ""), str(data.get("last_assistant_message") or ""))
    if not rec:
        return
    rec.update(ts=int(time.time()), agent_id=data.get("agent_id"), session_id=data.get("session_id"),
               head=gitsnap._git(repo, "rev-parse", "-q", "--verify", "HEAD") if repo else None)
    try:
        f = root / VERDICT_FILE
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def on_start(data: dict, repo: str) -> None:
    f = _state_file(repo, str(data.get("agent_id") or "unknown"))
    sha = gitsnap.snapshot(repo, f"harness snapshot at subagent start: {data.get('agent_type')}")
    if f and sha:
        f.write_text(sha)


def on_stop(data: dict, root: Path, repo) -> None:
    record_verdict(root, data, repo)
    if not repo:
        return
    f = _state_file(repo, str(data.get("agent_id") or "unknown"))
    if not f or not f.is_file():
        return
    start = f.read_text().strip()
    if data.get("stop_hook_active"):
        f.unlink(missing_ok=True)                      # 이미 한 번 세웠다 — 무한 반복하지 않는다
        return
    agent = str(data.get("agent_type") or "subagent")
    deleg = _delegation()
    policy = deleg.load_policy(root)
    if agent.rsplit(":", 1)[-1] in policy["writers"] or agent in policy["writers"]:
        f.unlink(missing_ok=True)
        return
    now = gitsnap.worktree_tree(repo)
    before = gitsnap._git(repo, "rev-parse", start + "^{tree}")
    if not now or not before or now == before:
        f.unlink(missing_ok=True)
        return
    hits = [p for p in gitsnap.changed_paths(repo, before, now)
            if deleg.in_code_zone(os.path.join(repo, p), root, policy["codeZone"])]
    if not hits:
        f.unlink(missing_ok=True)
        return
    listing = ", ".join(f"`{p}`" for p in hits[:10]) + (f" 외 {len(hits) - 10}개" if len(hits) > 10 else "")
    print(json.dumps({"decision": "block", "reason": (
        f"이 서브에이전트(`{agent}`)가 실행되는 동안 코드 영역이 바뀌었다: {listing}. RULES §7: 서브에이전트는 "
        "코드 영역을 쓰지 않는다(포매터·생성기·설치·스크립트로 바뀐 것도 같다).\n"
        f"다음 행동: 되돌리지 말고, 최종 보고 맨 앞에 이 파일 목록과 시작 시점 스냅샷 `{start[:12]}` 을 적고 "
        "어떤 명령이 바꿨는지 밝힌 뒤 끝내라. 같은 시간에 사용자·메인 세션이 고친 파일일 수 있으니 되돌릴지는 "
        f"메인 세션이 사용자와 판단한다(파일 단위 복구: `git show {start[:12]}:<경로> > <경로>`).")},
        ensure_ascii=False))


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or ".")
    repo = gitsnap.repo_root(str(root))
    event = data.get("hook_event_name")
    try:
        if event == "SubagentStart" and repo:
            on_start(data, repo)
        elif event == "SubagentStop":
            on_stop(data, root, repo)
    except Exception:
        pass                                           # fail-open — 감사가 작업을 세우지 않는다
    sys.exit(0)


if __name__ == "__main__":
    main()
