#!/usr/bin/env python3
"""SessionStart: 실행 시점 rm 가드(bin/rm)를 이 세션의 Bash PATH 앞에 둔다.

Claude Code 는 SessionStart 훅이 CLAUDE_ENV_FILE 에 쓴 내용을 이후 모든 Bash 명령 앞에 실행한다(공식 hooks 문서).
그래서 `rm`·`\\rm`·`command rm`·`xargs rm`·`find -exec rm` 이 모두 래퍼를 지난다. `/bin/rm` 처럼 경로를 직접
쓴 호출은 정적 가드(block-destructive-git.py)가 맡는다.

Claude Code 는 SessionStart 마다 env 파일을 따로 만들고(`<session-env>/sessionstart-hook-N.sh`) 세션 동안 전부
차례로 실행한다. 그래서 플러그인을 업데이트한 뒤 재개한 세션에는 옛 버전이 다른 파일에 남긴 PATH 줄이 살아 있고,
나중에 실행되는 쪽이 앞을 차지한다(실측: 0.38.0 설치 후 0.36.1 의 bin/rm 이 먼저 풀림). 같은 세션 디렉터리의
env 파일에서 **이 플러그인의 다른 버전이 쓴 PATH 줄만** 지운다 — 다른 훅이 쓴 줄은 건드리지 않는다.
"""
import glob
import json
import os
import re
import shlex
import sys

NAME = "react-supabase-harness"
OUR_PATH = re.compile(r"""^export PATH=(?P<q>'?)(?P<bin>[^'\n]+/bin)(?P=q):"\$PATH"\n""", re.M)


def lines(root: str, project: str) -> str:
    out = f'export PATH={shlex.quote(os.path.join(root, "bin"))}:"$PATH"\n'
    if project:                                          # cd 로 옮긴 뒤에도 원래 프로젝트를 지우지 못하게
        out += f"export HARNESS_PROJECT_DIR={shlex.quote(project)}\n"
    return out


def _cached_version(b: str) -> bool:
    """마켓플레이스 캐시 안의 이 플러그인 버전 bin 인가(`…/<마켓>/react-supabase-harness/<버전>/bin`).
    개발 설치(`--plugin-dir`)나 같은 폴더의 다른 체크아웃은 캐시 배치가 아니라 건드리지 않는다. plugin.json 이 깨졌거나
    지워진 옛 버전도 캐시 배치만으로 판단한다."""
    plugin_dir = os.path.dirname(b)
    if os.path.basename(os.path.dirname(plugin_dir)) != NAME:
        return False
    try:
        with open(os.path.join(plugin_dir, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
            name = json.load(f).get("name")
    except Exception:
        return True
    return name in (NAME, None)


def drop_stale(env_file: str, root: str) -> None:
    """같은 세션 env 디렉터리에서, 지금 버전이 아닌 이 플러그인의 캐시 버전 bin 을 앞에 두는 줄을 지운다."""
    here = os.path.join(root, "bin")
    for path in glob.glob(os.path.join(os.path.dirname(env_file), "*.sh")) + [env_file]:
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        new = OUR_PATH.sub(lambda m: "" if m.group("bin") != here and _cached_version(m.group("bin")) else m.group(0),
                           text)
        if new != text:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new)


def main() -> None:
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    if not env_file or not os.path.isfile(os.path.join(root, "bin", "rm")):
        return
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import scope                                         # 정적 가드와 같은 프로젝트 출처(CLAUDE_PROJECT_DIR → cwd)
    project = scope.project_root(data)
    want = lines(root, os.path.realpath(str(project)) if project else "")
    drop_stale(env_file, root)
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
