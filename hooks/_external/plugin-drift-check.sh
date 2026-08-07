#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# 하네스 가동 감시자 — **플러그인 밖**에서 도는 유일한 훅.
#
# 왜 밖에 있어야 하나
#   플러그인 안의 훅은 "플러그인 훅이 설치되지 않았다"를 감지할 수 없다. 감지하려면
#   자기가 돌아야 하는데, 안 돌고 있는 게 문제니까. 그래서 이 파일만 사용자 레벨
#   (`~/.claude/hooks/`)에 산다. 정본은 하네스 저장소가 들고 있고, 설치는 아래 참조.
#
# 설치 (1회)
#   cp hooks/_external/plugin-drift-check.sh ~/.claude/hooks/
#   ~/.claude/settings.json 의 SessionStart 에 등록:
#     bash ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/plugin-drift-check.sh
#
# 2026-08-07 개정 — 구판은 버전만 비교해서, 실제 사고를 통째로 놓쳤다:
#   설치본에 hooks/ 디렉터리가 **아예 없어** 강제 장치 8개가 0개 가동 중이었는데
#   메시지는 "드리프트: v0.16.0 != v0.17.0" 한 줄이었고, 세션이 읽고 지나갔다.
#   게다가 처방("claude plugin update")이 무효였다 — 강제 장치를 만든 커밋 2개가
#   로컬에만 있어 GitHub 에서 당겨봐야 그대로였다. 원인은 드리프트가 아니라 미푸시.
# ─────────────────────────────────────────────────────────────────────────────
python3 - <<'EOF'
import json, os, subprocess
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
SRC = HOME / "react-supabase-harness"
KEY = "react-supabase-harness@react-supabase"

def sh(args, cwd):
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""

def installed_root():
    """설치본 실경로. 캐시 배치가 바뀌어도 찾도록 후보를 훑는다."""
    for p in (HOME / ".claude/plugins/cache/react-supabase/react-supabase-harness",
              HOME / ".claude/plugins/repos/react-supabase/react-supabase-harness"):
        if p.is_dir():
            return p
    base = HOME / ".claude/plugins"
    hits = list(base.glob("*/*/react-supabase-harness")) if base.is_dir() else []
    return hits[0] if hits else None

problems = []   # (심각도, 사실, 처방)

inst = installed_root()
src_ver = None
try:
    src_ver = json.loads((SRC / ".claude-plugin/plugin.json").read_text())["version"]
except Exception:
    pass

# ── 1. 강제 장치가 실제로 가동 중인가 (가장 중요 — 구판이 놓친 것)
expected = []
try:
    hj = json.loads((SRC / "hooks/hooks.json").read_text())
    for ev in hj.get("hooks", {}).values():
        for grp in ev:
            for h in grp.get("hooks", []):
                cmd = h.get("command", "")
                if "hooks/" in cmd:
                    expected.append(cmd.rsplit("hooks/", 1)[-1].strip('"'))
except Exception:
    pass

if inst is None:
    problems.append(("CRIT", "설치본을 찾을 수 없다", "/plugin install react-supabase-harness"))
elif not (inst / "hooks").is_dir():
    problems.append(("CRIT",
        f"설치본에 hooks/ 가 없다 — 강제 장치 {len(expected)}개가 **0개 가동** 중",
        "아래 push → update 순서"))
else:
    missing = [h for h in expected if not (inst / "hooks" / h).is_file()]
    if missing:
        problems.append(("CRIT",
            f"설치본에 훅 {len(missing)}/{len(expected)}개 없음: {', '.join(missing)}",
            "아래 push → update 순서"))

# ── 2. 미푸시 — 이게 있으면 update 처방 자체가 무효다
unpushed = sh(["git", "log", "--oneline", "@{u}.."], SRC)
if unpushed:
    n = len(unpushed.splitlines())
    problems.append(("CRIT",
        f"소스에 미푸시 커밋 {n}개 — 플러그인은 GitHub 에서 설치되므로 update 해도 안 올라온다",
        f"cd {SRC} && git push   (그 다음에야 update 가 의미 있다)"))

# ── 3. 버전 드리프트
try:
    ins = json.loads((HOME / ".claude/plugins/installed_plugins.json").read_text())
    cache_ver = ins["plugins"][KEY][0]["version"]
    if src_ver and cache_ver != src_ver:
        problems.append(("WARN", f"버전 드리프트 설치본 v{cache_ver} != 소스 v{src_ver}",
                         "claude plugin update react-supabase-harness (재시작 후 적용)"))
except Exception:
    pass

if problems:
    crit = [p for p in problems if p[0] == "CRIT"]
    head = "🔴 하네스 강제 장치가 돌고 있지 않다" if crit else "⚠️ 하네스 드리프트"
    out = [head, ""]
    for _, fact, fix in problems:
        out.append(f"  · {fact}")
        out.append(f"      → {fix}")
    if crit:
        out += [
            "",
            "  지금 이 세션에는 block-impl-delegation(구현 위임 차단) · workflow-entry-guard",
            "  (플랜 없이 코드 시작 감지) · harness-boarding-guard(미탑승 적출) · stack-compliance",
            "  -guard 가 **하나도 붙어 있지 않다**. 규정은 컨텍스트에 상주하지 않으므로,",
            "  이 상태에서는 하네스 규약을 세션이 기억해야만 지켜진다 — 그건 3회 실패한 방식이다.",
            "",
            "  구현에 착수하기 전에 사용자에게 이 사실을 먼저 보고하라. 조용히 진행하지 말 것.",
        ]
    print("\n".join(out))
EOF
exit 0
