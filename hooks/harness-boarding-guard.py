#!/usr/bin/env python3
"""SessionStart hook — 하네스에 *탑승하지 않은* 프로젝트를 세션 시작에 적출한다.

배경(실측 사고):
    "하네스를 얹어서 진행"이 명시됐는데도 세션은 템플릿 파일만 손으로 복사하고 BOOTSTRAP
    시퀀스의 한 단계(`supabase start`)만 밟았다. 셋업 미실행 · repo 미생성 · plan 0개 ·
    `/phase 0` 미통과 상태로 스키마와 화면을 만들었고, 아무것도 울리지 않았다.
    대가는 화면 IA 재작업으로 돌아왔다.

왜 workflow-entry-guard 로 부족한가:
    그 훅은 "plan 없이 src/ 첫 코드"를 본다. 즉 **코드를 쓰기 시작해야** 발동한다.
    미탑승의 진짜 문제는 그 전에 이미 결정돼 있다 — 셋업을 안 했고 repo 가 없으면
    이후 `/issue`·`/branch`·`/pr` 이 붙을 데가 없다. 사슬의 첫 고리라 하나 놓치면 나머지가
    자동으로 다 빠진다. 그래서 **세션 시작**에 본다.

왜 매 세션 반복하는가:
    workflow-entry-guard 는 판단 여지가 있어(한 문장 diff 예외) 1회만 주입한다. 탑승은
    이분법이고 고치기 전까지 계속 깨져 있다. 탑승하면 그 순간부터 영원히 침묵한다.

침묵 조건(오탐이 신뢰를 깎지 않도록):
    react/supabase 프로젝트가 아님 · 하네스 저장소 자신 · 탑승 증거가 하나라도 있음.
"""
import json
import os
import sys
from pathlib import Path

# 훅 출력은 치환되지 않는다 — `${CLAUDE_PLUGIN_ROOT}` 를 그대로 내면 모델이 경로를 풀 수 없다.
# 훅 프로세스에는 같은 이름의 환경변수가 export 된다(plugins-reference#where-each-variable-resolves).
PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT") or str(Path(__file__).resolve().parent.parent)


def project_dir() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or ".").resolve()


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def is_target(root: Path) -> bool:
    """하네스가 관할하는 프로젝트인가. 아니면 아무 말도 하지 않는다.

    관할 범위를 좁게 잡는 이유 = 오탐이 이 훅을 죽인다. "react 를 쓰면 전부"로 잡으면
    단발 프로토타입 수십 개가 매 세션 발화하고, 그러면 아무도 이 메시지를 읽지 않게 된다.
    실제로 관할해야 하는 것은 **하네스 구조를 갖췄거나 Supabase 서비스인 프로젝트**다.

    제외:
      · 하네스 저장소 자신 (.claude-plugin/)
      · 템플릿 원본(마커 role=template) — `__PROJECT_NAME__` 이 살아 있는 게 정상이다
      · 하네스 구조도 Supabase 도 없는 단발 프로토타입
    """
    if (root / ".claude-plugin").is_dir():
        return False
    if not (root / "package.json").is_file():
        return False

    # 🔴 제외는 휴리스틱이 아니라 마커로 한다.
    #    첫 판은 package.json 의 name 이 템플릿 이름이면 템플릿으로 보고 제외했다.
    #    그런데 템플릿을 손으로 복사한 프로젝트는 name 도 그대로다 — 즉 **표적 집단을 정확히
    #    제외**해 버렸다(실측: 템플릿을 복사한 실프로젝트가 침묵). 추측하는 규칙은 이렇게 뒤집힌다.
    role = marker_role(root)
    if role in ("template", "prototype"):
        return False

    has_structure = (root / "docs" / "plans").is_dir() or (root / "docs" / "RULES.md").is_file()
    return has_structure or (root / "supabase").is_dir()


def marker_role(root: Path):
    """`.harness.json` 의 role. 없으면 None.

    project(기본) — 하네스 관할. 탑승 상태를 검사한다.
    prototype     — 단발 데모. 이슈·PR 플로우 대상이 아니다(사용자 결정을 파일로 기록).
    template      — 템플릿 원본. `__PROJECT_NAME__` 이 살아 있는 게 정상이다.
    """
    f = root / ".harness.json"
    if not f.is_file():
        return None
    try:
        return json.loads(read(f)).get("role", "project")
    except Exception:
        return "project"


def real_plans(root: Path) -> int:
    d = root / "docs" / "plans"
    if not d.is_dir():
        return 0
    return sum(1 for f in d.iterdir() if f.suffix == ".md" and not f.name.endswith(".template.md"))


def in_git_repo(root: Path) -> bool:
    """상위로 거슬러 올라가며 찾는다.

    바로 아래만 보면 모노레포 안의 하위 프로젝트를 전부 "git 없음"으로 오판한다
    (실측: 모노레포 하위 앱이 전부 오판). 오탐은 훅을 무시하게 만들고, 무시되는 훅은
    없는 훅이다 — 이 훅이 막으려는 실패와 정확히 같은 실패다.
    """
    for d in [root, *root.parents]:
        if (d / ".git").exists():
            return True
    return False


def scaffold_placeholder(root: Path):
    # 반환: 플레이스홀더가 살아 있는 파일명 또는 None.
    # 주의 — 훅은 Python 3.9 에서도 돈다(README 최소 버전). `str | None` (PEP 604) 금지.
    for name in ("CLAUDE.md", "package.json", "index.html", "README.md"):
        f = root / name
        if f.is_file() and "__PROJECT_NAME__" in read(f):
            return name
    return None


def diagnose(root: Path):
    # 반환: (확정 미탑승 hard, 탑승 흔적 없음 soft)
    hard, soft = [], []

    ph = scaffold_placeholder(root)
    if ph:
        hard.append(f"템플릿 치환 미완료 — `__PROJECT_NAME__` 이 {ph} 에 살아 있다(템플릿의 셋업 스크립트를 돌리거나 직접 치환)")
    if not in_git_repo(root):
        hard.append("git 저장소 없음 — /issue·/branch·/commit·/pr 이 붙을 데가 없다")

    if not (root / ".harness.json").is_file():
        soft.append("탑승 마커 `.harness.json` 없음 — 이 레포는 하네스 버전을 기록한 적이 없다")
    if real_plans(root) == 0:
        soft.append("`docs/plans/` 에 실제 plan 0개 (템플릿 제외) — /phase 를 밟은 적이 없다")

    return hard, soft


def main() -> None:
    root = project_dir()
    if not is_target(root):
        sys.exit(0)

    hard, soft = diagnose(root)
    # 마커만 있고 실제 plan 이 0개 = 탑승은 기록했으나 /phase 를 밟지 않은 채 구현 중일 수 있다.
    # 마커 존재만으로 침묵하면 "마커를 만들면 조용해진다"가 되어 이 훅이 막으려는 건너뛰기를 못 본다.
    marked_no_plan = (root / ".harness.json").is_file() and real_plans(root) == 0
    # 확정 지문이 있거나, 탑승 흔적이 아예 없거나, 마커만 있고 plan 이 없을 때 말한다.
    if not hard and len(soft) < 2 and not marked_no_plan:
        sys.exit(0)

    head = (f"🚧 하네스 탑승은 기록됐으나 /phase 를 밟지 않았다 — {root.name}"
            if marked_no_plan and not hard else f"🚧 하네스 미탑승 — {root.name}")
    lines = [head, ""]
    for h in hard:
        lines.append(f"  ❌ {h}")
    for s in soft:
        lines.append(f"  ⚠️  {s}")
    lines += [
        "",
        "이 상태에서 구현을 진행하면 `/phase 0`(도메인·IA → structure-fitness FIT 판정)이 통째로",
        "빠진다. 그 게이트는 '맞는 걸 만드는가'를 스키마 **전에** 거르는 자리이고, 건너뛴 대가는",
        "스키마가 아니라 화면 IA 재작업으로 나중에 돌아온다(실측).",
        "",
        f"지금 할 것 — `{PLUGIN_ROOT}/docs/BOOTSTRAP.md` 를 Read 하고 1~6 단계를 밟아라.",
        "  템플릿에서 시작했다면 그 템플릿의 셋업 스크립트를 먼저 돌린다(`__PROJECT_NAME__` 치환).",
        "  기존·직접 구성 레포는 `.harness.json` 을 만들어 탑승을 기록하고(형식은 BOOTSTRAP step 2), /phase 0 부터 밟는다.",
        "",
        "사용자가 '하네스 얹어서 진행'을 지시했다면 이 진단을 조용히 넘기지 말고 먼저 보고하라.",
        "탑승 기록과 실제 plan(`/phase` 산출물)이 둘 다 생기면 이 메시지는 다시 나오지 않는다.",
    ]

    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": "\n".join(lines),
    }}, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
