#!/usr/bin/env python3
"""PreToolUse hook — RULES §7 실효화: 서브에이전트는 코드 영역을 쓰지 않는다.

배경(실측): 메인 세션이 확정 스펙을 근거로 구현 전체를 하위 모델 서브에이전트에
위임했다. 산출물은 typecheck·test 를 통과했지만 모듈 경계가 무너져(표시 문구와 도메인
로직 혼재, 타입 산재, 함수 오배치) 사람이 육안으로 반복 적발했다.

왜 '스폰 프롬프트'가 아니라 '실제 쓰기'를 보나:
    이전 판은 Agent 스폰 프롬프트의 문구로 구현 지시인지 추측했다. 3차 리뷰에서 표현을
    조금만 바꿔도 놓치고, 결과 보고서를 "작성"하라는 정상 리서치 지시는 막는다는 것이
    양쪽으로 재현됐다(무해 프롬프트 19개 중 12개 거부). 문구는 의도의 대리 지표일 뿐이다.
    Claude Code 는 서브에이전트의 툴 호출에도 PreToolUse 를 부르고 입력에 `agent_id`·
    `agent_type` 을 싣는다(공식 hooks 문서). 그래서 결과 — 서브에이전트가 코드 파일을
    쓰려는 그 호출 — 를 직접 막는다. 어떻게 부탁했든 같은 결과는 같은 판정을 받는다.

판정 규칙:
- `agent_id` 가 없는 호출(메인 세션)은 통과.
- 서브에이전트의 Write·Edit·NotebookEdit 대상, Bash 의 쓰기 대상(리다이렉트·복사 목적지·
  in-place 편집)이 **코드 영역**이면 deny. 작업 트리를 바꾸는 git 명령도 같다.
- 코드 영역 밖(scratchpad·/tmp·결과 문서·docs/plans)은 통과 — 리서치 산출, planner 의
  plan 작성, 브라우저 조작 같은 기계 실행 위임은 막지 않는다.
- 코드 영역과 예외 타입은 데이터다: rules.jsonc 의 `delegation.codeZone`·`delegation.writers`.
  프로젝트 파일이 하네스 기본을 덮어쓴다. 그 파일은 gate-engine 이 에이전트 쓰기로부터
  보호하므로, 위임 예외는 사용자가 직접 추가한다.
- worktree 격리 에이전트는 프로젝트 루트 밖에서 일하므로 판정 대상이 아니다(머지는 리뷰를 거친다).

한계(정직하게): 인터프리터 한 줄 실행이나 스크립트 파일을 거친 쓰기는 Bash 문자열에서
대상이 드러나지 않아 놓칠 수 있다. 협조형 가드이며, 사후 diff 감사(SubagentStop)는 그런
우회가 실측될 때 추가한다(README 로드맵).
"""
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shellparse  # noqa: E402

DEFAULT_CODE_ZONE = [
    "src/*", "app/*", "pages/*", "components/*", "lib/*", "supabase/*", "packages/*",
    "tests/*", "test/*", "e2e/*", "styles/*", "theme/*", "index.html", "package.json",
    "tsconfig*.json", "*.config.*", "biome.json*", "vercel.json",
]
# 작업 트리를 통째로 바꾸는 git 하위 명령 — 대상 경로를 특정할 수 없으니 코드 영역 쓰기로 본다.
GIT_TREE_SUBS = {"apply", "am", "checkout", "switch", "restore", "reset", "merge", "rebase",
                 "cherry-pick", "revert", "pull", "clean"}
GIT_STASH_READ = {"list", "show"}


def load_policy(root: Path) -> dict:
    """하네스 기본 → 프로젝트 순으로 `delegation` 객체를 덮어쓴다. 읽기 실패는 기본값(fail-safe)."""
    policy = {"codeZone": list(DEFAULT_CODE_ZONE), "writers": []}
    paths = []
    pr = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if pr:
        paths.append(Path(pr) / "gates" / "rules.jsonc")
    paths.append(root / ".claude" / "gates" / "rules.jsonc")
    strip = _strip_jsonc()
    for p in paths:
        try:
            d = json.loads(strip(p.read_text(encoding="utf-8"))).get("delegation") or {}
        except Exception:
            continue
        for k in ("codeZone", "writers"):
            if isinstance(d.get(k), list):
                policy[k] = d[k]
    return policy


def _strip_jsonc():
    """gate-engine 의 문자열 인지 JSONC 스캐너를 재사용한다(정규식으로 벗기면 glob 이 지워진다)."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("gate_engine", Path(__file__).with_name("gate-engine.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.strip_jsonc
    except Exception:
        return lambda t: t


def in_code_zone(path: str, root: Path, zone: list) -> str:
    """프로젝트 루트 기준 상대 경로가 코드 영역이면 그 상대 경로를, 아니면 빈 문자열."""
    if not path:
        return ""
    p = os.path.expanduser(path)
    if not os.path.isabs(p):
        p = os.path.join(str(root), p)
    try:
        real, base = os.path.realpath(p), os.path.realpath(str(root))
    except Exception:
        return ""
    if not real.startswith(base + os.sep):
        return ""
    rel = os.path.relpath(real, base).replace(os.sep, "/")
    # 디렉터리 목적지(`cp x src/`)는 그 안에 쓰는 것이다 — `src` 도 `src/*` 영역으로 본다
    return rel if any(fnmatch.fnmatch(rel.lower(), g.lower()) or fnmatch.fnmatch(rel.lower() + "/_", g.lower())
                      for g in zone) else ""


def inside(path: Path, root: Path) -> bool:
    try:
        real, base = os.path.realpath(str(path)), os.path.realpath(str(root))
    except Exception:
        return False
    return real == base or real.startswith(base + os.sep)


def git_tree_write(seg: str, cwd: str, root: Path) -> bool:
    """git 이 이 프로젝트의 작업 트리를 바꾸는가. 문자열 속 `git checkout`(검색어)은 명령이 아니다."""
    toks = shellparse._tokens(seg)
    if not toks or toks[0].rsplit("/", 1)[-1] != "git":
        return False
    repo, i = cwd, 1
    while i < len(toks) and toks[i].startswith("-"):
        if toks[i] in ("-C", "-c", "--git-dir", "--work-tree") and i + 1 < len(toks):
            if toks[i] == "-C":
                d = os.path.expanduser(toks[i + 1])
                repo = d if os.path.isabs(d) else os.path.join(cwd, d)
            i += 2
        else:
            i += 1
    if i >= len(toks) or not inside(Path(repo), root):
        return False                                  # 다른 저장소
    sub = toks[i]
    if sub == "stash":
        return len(toks) == i + 1 or toks[i + 1] not in GIT_STASH_READ
    return sub in GIT_TREE_SUBS


def deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}, ensure_ascii=False))
    sys.exit(0)


def message(agent: str, target: str) -> str:
    return (f"서브에이전트(`{agent}`)는 코드 영역을 쓰지 않는다 — `{target}`. "
            "RULES §7: 구현은 메인 세션이 한다(위임 구현은 검증을 통과해도 모듈 경계가 무너지는 실패가 반복됐다).\n"
            "다음 행동: 변경안을 diff 나 코드 블록으로 최종 메시지에 담아 돌려줘라. 메인 세션이 검토해 적용한다. "
            "이 타입에 구현 위임이 정말 필요하면 사용자가 프로젝트 `.claude/gates/rules.jsonc` 의 "
            "`delegation.writers` 에 추가한다.")


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if not data.get("agent_id"):
        sys.exit(0)                                   # 메인 세션
    agent = str(data.get("agent_type") or "subagent")
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or ".")
    policy = load_policy(root)
    if agent.rsplit(":", 1)[-1] in policy["writers"] or agent in policy["writers"]:
        sys.exit(0)

    tool, ti = data.get("tool_name"), data.get("tool_input") or {}
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        hit = in_code_zone(ti.get("file_path") or ti.get("notebook_path") or "", root, policy["codeZone"])
        if hit:
            deny(message(agent, hit))
    elif tool == "Bash":
        cmd = str(ti.get("command") or "")
        for seg, here in shellparse.segments(cmd, str(data.get("cwd") or root)):
            if git_tree_write(seg, here, root):
                deny(message(agent, "git 작업 트리 변경"))
            for t in shellparse.write_targets(seg, cmd):
                t = os.path.expanduser(t)
                hit = in_code_zone(t if os.path.isabs(t) else os.path.join(here, t), root, policy["codeZone"])
                if hit:
                    deny(message(agent, hit))
    sys.exit(0)


if __name__ == "__main__":
    main()
