#!/usr/bin/env python3
"""하네스 스냅샷 정리 — 0.38.0 이전 스냅샷에는 미추적 비밀 파일(.env 등)과 명령 원문이 들어 있을 수 있다.

기본은 dry-run(목록만). 지우는 것은 되돌릴 수 없으니 사용자가 직접 실행한다.

  python3 scripts/purge-snapshots.py <레포>...            # 무엇이 있는지만 본다
  python3 scripts/purge-snapshots.py --apply <레포>...    # refs/harness/snapshots/* 와 표식 삭제
  python3 scripts/purge-snapshots.py --apply --prune <레포>...
        # + `git gc --prune=now` — 객체까지 지운다. 스냅샷과 무관한 도달 불가 객체(drop 한 stash 등
        #   아직 되살릴 수 있던 것)도 함께 사라진다.

ref 만 지우면 객체는 git gc 의 기본 유예(2주) 뒤에 회수된다. 스냅샷 ref 는 브랜치가 아니라 일반 push 에
실리지 않지만, `git push --mirror` 나 `refs/*` refspec 은 싣는다.
"""
import argparse
import os
import subprocess
import sys

REF_PREFIX = "refs/harness/snapshots/"


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repos", nargs="+")
    ap.add_argument("--apply", action="store_true", help="실제로 지운다(기본은 목록만)")
    ap.add_argument("--prune", action="store_true", help="--apply 와 함께: git gc --prune=now 까지")
    a = ap.parse_args()
    if a.prune and not a.apply:
        ap.error("--prune 은 --apply 와 함께만 쓴다")
    rc = 0
    for repo in a.repos:
        gd = git(repo, "rev-parse", "--absolute-git-dir")
        if not gd:
            print(f"{repo}: git 저장소가 아니다 — 건너뜀")
            rc = 1
            continue
        refs = (git(repo, "for-each-ref", "--format=%(refname)", REF_PREFIX) or "").split()
        marks = [p for p in (os.path.join(gd, "harness-last-snapshot"), os.path.join(gd, "harness-subagents"))
                 if os.path.exists(p)]
        print(f"{repo}: 스냅샷 ref {len(refs)}개" + (f", 표식 {', '.join(os.path.basename(m) for m in marks)}" if marks else ""))
        if not a.apply:
            continue
        for ref in refs:
            git(repo, "update-ref", "-d", ref)
        for m in marks:
            if os.path.isdir(m):
                for f in os.listdir(m):
                    os.remove(os.path.join(m, f))
                os.rmdir(m)
            else:
                os.remove(m)
        left = (git(repo, "for-each-ref", "--format=%(refname)", REF_PREFIX) or "").split()
        print(f"  ref 삭제 {len(refs) - len(left)}개" + (f" — 남음 {len(left)}" if left else ""))
        if a.prune:
            ok = git(repo, "gc", "--prune=now", "--quiet") is not None
            print("  git gc --prune=now " + ("완료" if ok else "실패"))
            rc |= 0 if ok else 1
    if not a.apply:
        print("\n(dry-run — 지우려면 --apply. 되돌릴 수 없다)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
