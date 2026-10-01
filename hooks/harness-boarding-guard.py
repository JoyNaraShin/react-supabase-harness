#!/usr/bin/env python3
"""SessionStart hook — 하네스에 *탑승하지 않은* 프로젝트를 세션 시작에 적출한다.

배경(2026-08-07 실측 사고):
    사용자가 "react-supabase-harness, react-supabase-stack 활용해서 실제 서비스처럼 진행"을
    명시했는데, 세션은 템플릿 파일만 손으로 복사하고 BOOTSTRAP 8단계 중 1개(`supabase start`)
    만 밟았다. `setup.sh` 미실행 · repo 미생성 · plan 0개 · `/phase 0` 미통과 상태로 스키마와
    화면 9개를 만들었고, 아무것도 울리지 않았다. 대가는 IA 재작업 2회(사용자 육안 반려).

왜 workflow-entry-guard 로 부족한가:
    그 훅은 "plan 없이 src/ 첫 코드"를 본다. 즉 **코드를 쓰기 시작해야** 발동한다.
    미탑승의 진짜 문제는 그 전에 이미 결정돼 있다 — setup.sh 를 안 돌렸고 repo 가 없으면
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
      · 템플릿 원본 react-supabase-stack — `__PROJECT_NAME__` 이 살아 있는 게 정상이다
      · 하네스 구조도 Supabase 도 없는 단발 프로토타입
    """
    if (root / ".claude-plugin").is_dir():
        return False
    if not (root / "package.json").is_file():
        return False

    # 🔴 제외는 휴리스틱이 아니라 마커로 한다.
    #    첫 판은 package.json 의 name 이 "react-supabase-stack" 이면 템플릿으로 보고 제외했다.
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

    바로 아래만 보면 모노레포·포폴 허브 안의 하위 프로젝트를 전부 "git 없음"으로 오판한다
    (실측: 모노레포 하위 앱이 전부 오판). 오탐은 훅을 무시하게 만들고, 무시되는 훅은
    없는 훅이다 — 이 훅이 막으려는 실패와 정확히 같은 실패다.
    """
    for d in [root, *root.parents]:
        if (d / ".git").exists():
            return True
    return False


def scaffold_placeholder(root: Path):
    # 반환: 플레이스홀더가 살아 있는 파일명 또는 None.
    # 주의 — 시스템 python3 가 3.9 라 `str | None` (PEP 604) 을 못 쓴다. 훅은 표준 3.9 로만.
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
        hard.append(f"setup.sh 미실행 — `__PROJECT_NAME__` 이 {ph} 에 살아 있다")
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
    # 확정 지문이 있거나, 탑승 흔적이 아예 없을 때만 말한다.
    if not hard and len(soft) < 2:
        sys.exit(0)

    lines = [f"🚧 하네스 미탑승 — {root.name}", ""]
    for h in hard:
        lines.append(f"  ❌ {h}")
    for s in soft:
        lines.append(f"  ⚠️  {s}")
    lines += [
        "",
        "이 상태에서 구현을 진행하면 `/phase 0`(도메인·IA → structure-fitness FIT 판정)이 통째로",
        "빠진다. 그 게이트는 '맞는 걸 만드는가'를 스키마 **전에** 거르는 자리이고, 건너뛴 대가는",
        "스키마가 아니라 화면 IA 재작업으로 나중에 돌아온다(2026-08-07 실측).",
        "",
        "지금 할 것 — `${CLAUDE_PLUGIN_ROOT}/docs/BOOTSTRAP.md` 를 Read 하고 1~6 단계를 밟아라.",
        "  신규   gh repo create <slug> --template JoyNaraShin/react-supabase-stack --private --clone",
        "        cd <slug> && ./scripts/setup.sh \"<Project Name>\" <slug>",
        "  기존   레포에서 `.harness.json` 을 만들어 탑승을 기록하고(버전·날짜), /phase 0 부터 밟는다",
        "",
        "사용자가 '하네스 얹어서 진행'을 지시했다면 이 진단을 조용히 넘기지 말고 먼저 보고하라.",
        "탑승을 마치면 이 메시지는 다시 나오지 않는다.",
    ]

    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": "\n".join(lines),
    }}, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
