#!/usr/bin/env python3
"""PostToolUse hook — biome 으로 편집 파일을 즉시 자동 포맷.

포맷 드리프트로 CI 가 깨지는 것을 hook 단계에서 차단(편집 직후 단일 파일 포맷).
대상: src/**/*.{ts,tsx,js,jsx,json,css} + 루트 biome/tsconfig.
실패해도 워크플로 차단 안 함 (exit 0). 프로젝트에 biome 이 없으면 silent skip.
"""
import json
import subprocess
import sys

EXTS = (".ts", ".tsx", ".js", ".jsx", ".json", ".css")


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

    try:
        # biome check --write — 포맷 + safe lint fix. 단일 파일이라 빠름.
        subprocess.run(
            ["npx", "@biomejs/biome", "check", "--write", path],
            timeout=10,
            check=False,
            capture_output=True,
        )
    except Exception:
        pass  # 실패해도 workflow 차단 안 함

    sys.exit(0)


if __name__ == "__main__":
    main()
