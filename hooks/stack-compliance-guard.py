#!/usr/bin/env python3
"""PostToolUse hook — RULES §8 스택 규칙 미준수를 코드 작성 중에 적출한다.

배경(2026-07-30 실측 사고, 같은 원인 2회째): `workflow-entry-guard` 는 **플랜 유무**로만
미탑승을 판정한다. 한 실프로젝트는 planner 가 쓴 플랜이 있었으므로 그 훅이 통과시켰고,
그 사이 메인 세션은 `docs/RULES.md` 를 한 번도 읽지 않은 채 화면 3개를 구현했다 —
Tailwind 대신 순수 CSS, `@/*` alias 없음, TanStack Query 없음. 사용자가 육안으로 적발.
플랜이 있어도 스택 규칙은 안 읽힌다. 그래서 "플랜 있음"이 아니라 **스택 신호 자체**를 본다.

검사 대상은 기계로 확인 가능한 §8 불변식만: Tailwind · `@/*` alias · TanStack Query.
차단이 아니라 주입이다 — 프로젝트 사정으로 예외를 둘 수 있고, 그 판단은 사용자 몫이다.
프로젝트당 1회만 알린다.
"""
import json
import os
import sys
from pathlib import Path

CODE_EXT = (".ts", ".tsx", ".jsx")


def project_dir() -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or ".")


def state_file() -> Path:
    return project_dir() / ".claude" / "state" / "stack-compliance.json"


def package_jsons(root: Path) -> list[dict]:
    # 모노레포도 있으므로 루트 + 1단계 워크스페이스까지 본다(node_modules 는 제외).
    candidates = [root / "package.json"]
    for group in ("apps", "packages"):
        base = root / group
        if base.is_dir():
            candidates += [d / "package.json" for d in base.iterdir() if d.is_dir()]
    out = []
    for p in candidates:
        try:
            out.append(json.loads(p.read_text()))
        except Exception:
            continue
    return out


def all_deps(pkgs: list[dict]) -> set[str]:
    names: set[str] = set()
    for pkg in pkgs:
        for field in ("dependencies", "devDependencies"):
            names |= set((pkg.get(field) or {}).keys())
    return names


def has_alias(root: Path) -> bool:
    for path in root.rglob("tsconfig*.json"):
        if "node_modules" in path.parts:
            continue
        try:
            text = path.read_text()
        except Exception:
            continue
        if '"@/*"' in text:
            return True
    return False


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    if data.get("tool_name") not in ("Write", "Edit"):
        sys.exit(0)

    path = (data.get("tool_input") or {}).get("file_path") or ""
    is_src_code = ("/src/" in path or path.startswith("src/")) and path.endswith(CODE_EXT)
    if not is_src_code or state_file().exists():
        sys.exit(0)

    root = project_dir()
    pkgs = package_jsons(root)
    deps = all_deps(pkgs)

    # React 앱이 아니면 §8(FE 컨벤션)의 적용 대상이 아니다 — 엔진·CLI 전용 repo 오탐 방지.
    if "react" not in deps:
        sys.exit(0)

    missing = []
    if "tailwindcss" not in deps:
        missing.append("Tailwind v4(§8: 외부 UI 라이브러리 금지 · Tailwind + 자체 primitives만)")
    if "@tanstack/react-query" not in deps:
        missing.append("TanStack Query(§스택 — 서버 상태를 수기 useEffect+fetch 로 짜지 않는다)")
    if not has_alias(root):
        missing.append("`@/*` → `./src/*` alias(§8)")

    if not missing:
        sys.exit(0)

    msg = (
        "⚠️  RULES §8 스택 규칙 미준수 신호 — 지금 `${CLAUDE_PLUGIN_ROOT}/docs/RULES.md` §8 을 Read 하라. "
        "누락: " + " / ".join(missing) + ". "
        "프로젝트 사정으로 예외가 필요하면 **사용자에게 물어 결정하고 RULES 에 예외를 기록**한다 — "
        "말없이 다른 스택으로 진행하지 말 것(2026-07-30: 같은 원인 2회째 재발, 화면 3개를 다시 씀). "
        "디렉터리 트리는 강제 아님(목적별 분리면 충족). (이 안내는 프로젝트당 1회.)"
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": msg,
    }}, ensure_ascii=False))
    p = state_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"notified": True, "missing": missing}, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
