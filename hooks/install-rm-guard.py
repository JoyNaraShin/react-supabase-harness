#!/usr/bin/env python3
"""SessionStart: 실행 시점 rm 가드(bin/rm)를 이 세션의 Bash PATH 앞에 둔다.

Claude Code 는 SessionStart 훅이 CLAUDE_ENV_FILE 에 쓴 내용을 이후 모든 Bash 명령 앞에 실행한다(공식 hooks 문서).
그래서 `rm`·`\\rm`·`command rm`·`xargs rm`·`find -exec rm` 이 모두 래퍼를 지난다. `/bin/rm` 처럼 경로를 직접
쓴 호출은 정적 가드(block-destructive-git.py)가 맡는다.
"""
import json
import os
import shlex
import sys


def lines(root: str, project: str) -> str:
    out = f'export PATH={shlex.quote(os.path.join(root, "bin"))}:"$PATH"\n'
    if project:                                          # cd 로 옮긴 뒤에도 원래 프로젝트를 지우지 못하게
        out += f"export HARNESS_PROJECT_DIR={shlex.quote(project)}\n"
    return out


def main() -> None:
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    if not env_file or not os.path.isfile(os.path.join(root, "bin", "rm")):
        return
    try:
        project = json.load(sys.stdin).get("cwd") or ""
    except Exception:
        project = ""
    want = lines(root, os.path.realpath(project) if project else "")
    try:
        with open(env_file, encoding="utf-8") as f:
            if want in f.read():
                return                                  # 재개·압축으로 다시 불려도 한 번만
    except FileNotFoundError:
        pass
    with open(env_file, "a", encoding="utf-8") as f:   # 다른 훅이 쓴 변수를 지우지 않게 덧붙인다
        f.write(want)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass                                             # 세션 시작을 막지 않는다 — 정적 가드는 그대로 동작한다
    sys.exit(0)
