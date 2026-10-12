"""관할 판정 — 어느 훅이 어느 프로젝트에서 도는가를 한 곳에서 정한다.

이 플러그인은 사용자 스코프로 설치되어 **모든 세션**에서 돈다. 그런데 훅마다 "이 프로젝트가 하네스
관할인가"를 따로 묻거나(boarding-guard·gate-engine) 아예 묻지 않아서(위임 차단·파일 산출 강제 등),
하네스와 무관한 레포에서도 워크플로 규칙이 발동했다. 그래서 훅을 두 계층으로 나눈다.

  T0 전역      — 사용자의 하드룰 집행자. 어느 레포에서든 돈다(파괴 명령·커밋 승인·정정·스냅샷·
                 프로젝트 룰 파일·판정 기록·리뷰 프로토콜).
  T1 하네스만  — 워크플로 규칙(plan·위임·리뷰·포맷). `kind(root) == "harness"` 일 때만 돈다.
                 하네스 레포 자신(`self`)에는 SELF_APPLIES 만 적용한다.
  OWN 자체 술어 — 자기 조건으로 판단한다(workflow-entry-guard).

판정 단위는 **세션 프로젝트**다 — 쓰기 대상 경로가 속한 프로젝트가 아니다. 가드는 각 훅의 `main()`
첫머리에 둔다(모듈 상단에서 끝내면 그 훅을 exec 로 불러 쓰는 다른 훅까지 죽는다).
"""
import json
import os
from pathlib import Path

T0, T1, OWN = "T0", "T1", "OWN"

# hooks.json 의 모든 command 는 아래 표에 정확히 한 번 나온다(tests: 표 완전성).
# 한 파일 안에 계층이 섞인 훅은 부분 이름(`훅.부분`)으로 나눈다.
TIERS = {
    "report-failures-gate": T0,
    "block-destructive-git": T0,
    "install-rm-guard": T0,
    "snapshot-guard": T0,
    "gate-engine": T0,                    # harnessTarget 룰은 룰 자체의 술어로 T1 처럼 동작
    "review-protocol": T0,
    "subagent-audit.verdict": T0,         # verdicts.jsonl — T0 소비자(verify FAIL 커밋 차단·no-pr-with-critical)
    "session-start-summary.commit-notice": T0,
    "block-impl-delegation": T1,
    "require-agent-output-file": T1,
    "commit-boundary-gate": T1,
    "subagent-audit.diff": T1,
    "security-nudge": T1,
    "stack-compliance-guard": T1,
    "edit-counter": T1,
    "format-on-edit": T1,
    "session-start-summary.status": T1,
    "harness-boarding-guard": T1,
    "workflow-entry-guard": OWN,
}

SELF_NAME = "react-supabase-harness"
SELF_APPLIES = {"commit-boundary-gate", "block-impl-delegation"}
SELF_CODE_ZONE = ["hooks/*", "bin/*", "tests/*"]


def project_root(data):
    """세션 프로젝트 루트. CLAUDE_PROJECT_DIR → 페이로드 cwd → None (모르면 모른다고 한다)."""
    for c in (os.environ.get("CLAUDE_PROJECT_DIR"), (data or {}).get("cwd")):
        if c:
            try:
                return Path(c).resolve()
            except Exception:
                continue
    return None


def marker_role(root):
    """`.harness.json` 의 role. 파일이 없으면 None, 읽을 수 없으면 project."""
    f = Path(root) / ".harness.json"
    if not f.is_file():
        return None
    try:
        role = json.loads(f.read_text(encoding="utf-8", errors="ignore")).get("role", "project")
        return role if isinstance(role, str) else "project"
    except Exception:
        return "project"


def _plugin_name(root):
    try:
        return json.loads((Path(root) / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")).get("name")
    except Exception:
        return None


def kind(root):
    """"self" | "template" | "prototype" | "harness" | "other"."""
    if root is None:
        return "other"
    r = Path(root)
    if (r / ".claude-plugin").is_dir():
        return "self" if _plugin_name(r) == SELF_NAME else "other"
    role = marker_role(r)
    if role in ("template", "prototype"):
        return role
    if role is not None:
        return "harness"                  # 마커로 탑승을 기록했으면 구조가 없어도 관할
    if not (r / "package.json").is_file():
        return "other"
    structured = (r / "docs" / "plans").is_dir() or (r / "docs" / "RULES.md").is_file()
    return "harness" if structured or (r / "supabase").is_dir() else "other"


def is_harness(root):
    return kind(root) == "harness"


def applies(hook, root):
    """이 훅(또는 `훅.부분`)이 이 루트에서 도는가. 표에 없는 이름은 KeyError — 조용히 넘어가지 않는다."""
    tier = TIERS[hook]
    if tier in (T0, OWN):
        return True
    k = kind(root)
    if k == "self":
        return hook.split(".", 1)[0] in SELF_APPLIES
    return k == "harness"
