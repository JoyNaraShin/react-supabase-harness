#!/usr/bin/env python3
"""PostToolUse hook — biome 으로 편집 파일을 즉시 자동 포맷.

포맷 드리프트로 CI 가 깨지는 것을 hook 단계에서 차단(편집 직후 단일 파일 포맷).
대상: src/**/*.{ts,tsx,js,jsx,json,css} + 루트 biome/tsconfig.
실패해도 워크플로 차단 안 함 (exit 0).

biome 은 **프로젝트-로컬(node_modules/.bin/biome)** 에 설치된 경우에만 실행한다.
설치돼 있지 않으면 silent skip — `npx` 로 네트워크 설치를 트리거하거나
biome 을 쓰지 않는 프로젝트의 파일을 멋대로 재포맷하지 않는다.

트레이드오프(2026-07-13 리뷰 S3, 평가 후 유지): --write 가 편집 직후 파일을
재작성하면 다음 Edit 이 stale 로 재-Read 를 요구할 수 있다. 그러나 (1) biome 은
멱등이라 이미 포맷된 파일은 재작성 없음 → 드리프트는 방금 쓴 미포맷 코드에만
잠깐 발생, (2) 하네스가 자동 재-Read 로 처리, (3) "작업 트리는 항상 CI-safe" 라는
이득이 그 마찰을 상회. 커밋-타임 포맷으로 옮기면 이 이득을 잃어 유지로 결정.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

EXTS = (".ts", ".tsx", ".js", ".jsx", ".json", ".css")


def find_local_biome(start: str) -> str | None:
    """편집 파일에서 위로 올라가며 프로젝트-로컬 biome 바이너리를 찾는다.

    로컬에 설치된 경우에만 경로를 돌려준다(없으면 None → 호출부에서 skip).
    이렇게 해서 네트워크 설치(npx @biomejs/biome)를 절대 트리거하지 않는다.
    """
    d = os.path.dirname(os.path.abspath(start))
    while True:
        for name in ("biome", "biome.cmd", "biome.exe"):
            cand = os.path.join(d, "node_modules", ".bin", name)
            if os.path.isfile(cand):
                return cand
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    tool = data.get("tool_name", "")
    if tool not in ("Edit", "Write"):
        sys.exit(0)

    path = (data.get("tool_input") or {}).get("file_path") or ""
    if not path or not path.endswith(EXTS):
        sys.exit(0)

    # src/ 외부(config, scripts 등)는 스킵 — 잡음 방지. 단 루트 biome/tsconfig 는 허용.
    if "/src/" not in path and not path.endswith(
        ("/biome.json", "/biome.jsonc", "/tsconfig.json")
    ):
        sys.exit(0)

    biome = find_local_biome(path)
    if not biome:
        sys.exit(0)  # 로컬 biome 없음 → 네트워크 설치 대신 skip

    try:
        # biome check --write — 포맷 + safe lint fix. 단일 파일이라 빠름.
        subprocess.run(
            [biome, "check", "--write", path],
            timeout=10,
            check=False,
            capture_output=True,
        )
    except Exception:
        pass  # 실패해도 workflow 차단 안 함

    sys.exit(0)


if __name__ == "__main__":
    main()
