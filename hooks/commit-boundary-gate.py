#!/usr/bin/env python3
"""Stop hook — 미커밋 변경이 리뷰 불가능한 크기로 불어나면 경계 쪼개기를 요구한다.

집행 대상 = `skills/commit/SKILL.md` (RULES §9·§12)
    §2 「여러 관심사 섞이면 **경계 쪼개기 제안**(한 커밋 = 한 관심사)」 · 「40+ 파일이면 경계 재검토 제안」
    §7 금지 「여러 관심사 한 커밋」

왜 규칙이 아니라 훅인가 (실측):
    규칙은 **이미 있었다.** 그런데 `commit` 스킬 안에만 있었고, 그 스킬은
    `disable-model-invocation: true` 라 **사용자가 `/commit` 을 칠 때만** 발동한다.
    즉 경계 검사가 커밋 시점에만 돈다 — 작업 중에는 아무도 안 본다.
    두 관심사가 중간 커밋 없이 같은 파일에 겹쳐 수십 개가 쌓였고, 리뷰가 불가능하다는
    지적을 받기 전까지 세션은 한 번도 경계를 제안하지 않았다. 세션의 자가 검출률은 0이었다.
    (같은 기제 = `report-failures-gate.py` — 규칙 텍스트로 막힐 결함이 아니다.)

판정 (결정론적 — LLM 판정 없음. gate-engine.py 설계 원칙 ①):
    `git status --porcelain` 의 변경 파일 수 ≥ 임계치. 세는 것 말고는 아무 판단도 하지 않는다.

🔴 오탐 방지 (오탐은 게이트를 죽인다):
    · **차단하지 않는다 — 알림만.** 파일이 쌓인 것은 사용자가 지시한 작업의 결과일 수 있고,
      차단하면 파일을 지우거나 커밋할 때까지 매 턴 막힌다. 그건 게이트를 즉시 끄게 만든다.
    · **띠(band)당 한 번만** 운다. 20↑ 에서 한 번, 40↑ 로 넘어갈 때 한 번. 같은 띠에서 반복 금지.
    · 최종 답변이 이미 경계를 제안했으면 통과 — 그리고 그 띠를 '알림 완료'로 기록해 다음 턴에도 조용하다.
    · 커밋되어 수가 내려가면 상태를 지운다 → 다시 쌓이면 다시 운다.
    · git 저장소가 아니거나 명령이 실패하면 통과(fail-open).
    · `stop_hook_active` 면 통과 — 무한 루프 금지.

탈출구 (끄는 것은 사용자 결정):
    `<repo>/.claude/state/commit-boundary-gate-off`
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# 띠. 20 = 슬라이스 둘이 겹친 지점(이 워크플로의 한 슬라이스는 대개 3~10파일).
# 40 = commit 스킬이 이미 못 박아 둔 "경계 재검토" 선.
BANDS = (20, 40)

# 경계를 이미 제안했다고 인정하는 어휘. 넓게 잡는다 — 목적은 문체 강제가 아니라 누락 방지다.
PROPOSED = re.compile(
    r"커밋 ?경계|경계 ?(쪼개|나누|가르|재검토)|커밋 ?(단위|분리|쪼개|나누)|"
    r"단위로 ?(나누|쪼개|가르)|스테이징|staged|commit boundar|split.{0,12}commit",
    re.IGNORECASE,
)


def out(msg: str) -> None:
    # 모델이 읽어야 하는 지시다. systemMessage 는 사용자에게만 보이므로 additionalContext 로
    # 보낸다 — https://code.claude.com/docs/en/hooks#stop-decision-control
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "Stop", "additionalContext": msg,
    }}, ensure_ascii=False))


def git(repo: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(
            ("git", "-C", str(repo), *args),
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return None
    return r.stdout if r.returncode == 0 else None


def final_text(data: dict) -> str:
    """최종 assistant 텍스트. stdin 의 last_assistant_message 가 정본이고, transcript 는
    비동기로 쓰여 이번 턴 답변이 아직 없을 수 있어 폴백으로만 쓴다.
    못 읽으면 빈 문자열(= 제안 안 한 것으로 본다)."""
    lam = data.get("last_assistant_message")
    if isinstance(lam, str) and lam.strip():
        return lam
    path = data.get("transcript_path")
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            lines = [l for l in f if l.strip().startswith("{")]
    except Exception:
        return ""
    last: list[str] = []
    for line in lines:
        try:
            j = json.loads(line)
        except Exception:
            continue
        if j.get("type") != "assistant":
            continue
        m = j.get("message") or {}
        bs = m.get("content") if isinstance(m, dict) else None
        if not isinstance(bs, list):
            continue
        texts = [b.get("text", "") for b in bs if isinstance(b, dict) and b.get("type") == "text"]
        if texts:
            last = texts
    return " ".join(last)


def spread(entries: list[str]) -> list[tuple[str, int]]:
    """변경이 어느 디렉터리에 몰렸는지. 경계를 세울 때 쓰는 재료라 세어서 같이 보여 준다."""
    counts: dict[str, int] = {}
    for e in entries:
        # porcelain: `XY path` / 이름 변경은 `XY old -> new`
        p = e[3:].split(" -> ")[-1].strip().strip('"')
        d = str(Path(p).parent)
        counts[d] = counts.get(d, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if data.get("stop_hook_active"):
        sys.exit(0)

    cwd = data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    top = git(Path(cwd), "rev-parse", "--show-toplevel")
    if not top:
        sys.exit(0)  # git 저장소가 아니다 — fail-open
    repo = Path(top.strip())

    if (repo / ".claude" / "state" / "commit-boundary-gate-off").exists():
        sys.exit(0)

    # `normal` = 사용자가 `git status` 로 보는 것과 같은 수. `all` 은 추적 안 되는 디렉터리를
    # 파일 단위로 펴서 수를 부풀린다(.gitignore 에서 새어 나간 빌드 산출물 하나로 오탐).
    porcelain = git(repo, "status", "--porcelain", "--untracked-files=normal")
    if porcelain is None:
        sys.exit(0)  # fail-open
    entries = [l for l in porcelain.splitlines() if l.strip()]
    n = len(entries)

    # 상태는 대상 저장소 밖에 둔다 — 저장소 안에 쓰면 그 파일 자체가 미커밋 변경으로 잡히고,
    # 하네스와 무관한 저장소에도 흔적을 남긴다.
    key = hashlib.sha1(str(repo).encode()).hexdigest()[:16]
    state_file = Path.home() / ".claude" / "state" / "commit-boundary" / f"{key}.json"
    try:
        state = json.loads(state_file.read_text())
    except Exception:
        state = {}
    notified = int(state.get("band") or 0)

    band = 0
    for b in BANDS:
        if n >= b:
            band = b

    def remember(value: int) -> None:
        try:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(json.dumps({"band": value, "count": n}, ensure_ascii=False))
        except Exception:
            pass

    # 커밋되어 내려갔다 → 상태를 지운다. 다시 쌓이면 다시 운다.
    if band == 0:
        if notified:
            remember(0)
        sys.exit(0)

    # 같은 띠에서는 이미 울었다. 띠가 내려갔으면(일부 커밋) 기록도 내려서,
    # 다시 그 위로 쌓일 때 다시 울게 한다.
    if band < notified:
        remember(band)
        sys.exit(0)
    if band == notified:
        sys.exit(0)

    # 이미 경계를 제안했으면 조용히 넘어가되, 이 띠는 처리된 것으로 기록한다.
    if PROPOSED.search(final_text(data)):
        remember(band)
        sys.exit(0)

    remember(band)
    top_dirs = spread(entries)[:5]
    listed = "\n".join(f"  · {d or '.'} — {c}개" for d, c in top_dirs)
    more = "" if len(top_dirs) >= len(spread(entries)) else "\n  (외 디렉터리 더 있음)"
    hard = band >= 40

    out(
        f"{'🚨' if hard else '⚠️'} 미커밋 변경 **{n}개**"
        f"{' — commit 스킬이 못 박은 40+ 재검토선을 넘었다' if hard else ''}.\n"
        f"{listed}{more}\n\n"
        "`skills/commit/SKILL.md` §2 「여러 관심사 섞이면 **경계 쪼개기 제안**"
        "(한 커밋 = 한 관심사)」 · §7 금지 「여러 관심사 한 커밋」.\n"
        "그 규칙은 `/commit` 을 칠 때만 도는데, **경계는 지금 세워야 리뷰가 가능하다** — "
        "쌓인 뒤에는 같은 파일 안에 두 관심사가 섞여 되돌리기 어렵다.\n\n"
        "**다음 답변에서 관심사별 커밋 단위를 제안하라** — 단위마다 정확한 파일 목록과 "
        "제목 초안. 커밋은 사용자 승인을 거쳐 `/commit` 스킬로 한다.\n"
        "이 알림을 끄는 것은 사용자 결정이다(`.claude/state/commit-boundary-gate-off`)."
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
