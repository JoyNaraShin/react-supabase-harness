#!/usr/bin/env python3
"""snapshot-guard 재현 벤치 — 두 트리의 '찍을지' 판정 비율과 PreToolUse 훅 지연을 같은 명령 표본으로 비교한다.

README·CHANGELOG 의 스냅샷 비용·비율 수치를 각자의 기계에서 다시 잴 수 있게 한다(감사 F18).

    python3 scripts/bench-snapshot.py --repo ~/Projects/my-app            # HEAD vs 작업 트리, 내 transcript 명령
    python3 scripts/bench-snapshot.py --repo . --base v0.40.1 --n 500 --corpus cmds.json

- 명령 표본: --corpus(JSON 문자열 배열) 또는 ~/.claude/projects/*/*.jsonl 의 Bash 명령(중복 제거·2000자 이하).
  표본은 기계 밖으로 나가지 않는다.
- 대상 레포는 임시 디렉터리에 `git clone --local` 한 사본만 쓴다(원본에 스냅샷 ref 를 만들지 않는다).
- 비율은 in-process(`snapshot-guard._repos`), 지연은 훅을 실제 프로세스로 띄워 잰다(파이썬 기동 포함).
- 사본의 트리는 바뀌지 않으므로 같은 트리 재사용 경로의 비용이다 — 파일이 바뀌는 실사용보다 스냅샷 1회가 싸다.
"""
import argparse
import glob
import importlib.util
import json
import os
import random
import subprocess
import sys
import tarfile
import tempfile
import time
from io import BytesIO

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def corpus(path, n, seed):
    if path:
        cmds = json.load(open(path, encoding="utf-8"))
    else:
        cmds = []
        for f in glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")):
            try:
                for ln in open(f, encoding="utf-8", errors="ignore"):
                    if '"Bash"' not in ln:
                        continue
                    try:
                        o = json.loads(ln)
                    except ValueError:
                        continue
                    for b in (o.get("message") or {}).get("content") or []:
                        if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Bash":
                            c = (b.get("input") or {}).get("command")
                            if isinstance(c, str) and len(c) <= 2000:
                                cmds.append(c)
            except OSError:
                pass
        cmds = list(dict.fromkeys(cmds))
    random.Random(seed).shuffle(cmds)
    return cmds[:n]


def tree_of(ref, tmp):
    """ref 의 hooks/ 를 tmp 에 꺼낸다. ref 가 None 이면 작업 트리 그대로."""
    if ref is None:
        return ROOT
    out = os.path.join(tmp, "base")
    os.makedirs(out)
    data = subprocess.run(["git", "-C", ROOT, "archive", ref, "hooks"], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=BytesIO(data)) as t:
        t.extractall(out)
    return out


def ratio(tree, cmds, repo):
    sys.path.insert(0, os.path.join(tree, "hooks"))
    for m in ("shellparse", "gitsnap"):
        sys.modules.pop(m, None)
    spec = importlib.util.spec_from_file_location("sg_" + str(abs(hash(tree))), os.path.join(tree, "hooks", "snapshot-guard.py"))
    sg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sg)
    sys.path.pop(0)
    hits = errors = 0
    for c in cmds:
        try:
            hits += bool(sg._repos(c, repo, os.path.realpath(repo)))
        except Exception:
            errors += 1
    return hits, errors


def latency(tree, cmds, repo):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=repo, CLAUDE_PLUGIN_ROOT=tree)
    hook = os.path.join(tree, "hooks", "snapshot-guard.py")
    ms = []
    for c in cmds:
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": c}, "cwd": repo,
                              "hook_event_name": "PreToolUse", "session_id": "bench", "transcript_path": os.devnull})
        t = time.perf_counter()
        subprocess.run([sys.executable, hook], input=payload, text=True, capture_output=True, cwd=repo, env=env, timeout=60)
        ms.append((time.perf_counter() - t) * 1000)
    s = sorted(ms)
    q = lambda p: round(s[min(len(s) - 1, int(p * len(s)))], 1)
    return {"mean": round(sum(s) / len(s), 1), "p50": q(.5), "p90": q(.9), "p95": q(.95), "p99": q(.99)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", required=True, help="스냅샷 대상 레포(임시 사본만 쓴다)")
    ap.add_argument("--base", default="HEAD", help="비교 기준 git ref(기본 HEAD)")
    ap.add_argument("--corpus", help="명령 JSON 배열(없으면 로컬 transcript)")
    ap.add_argument("--n", type=int, default=500, help="지연 표본 수(기본 500)")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "repo")
        subprocess.run(["git", "clone", "-q", "--local", os.path.abspath(a.repo), repo], check=True)
        all_cmds = corpus(a.corpus, 10 ** 9, a.seed)
        sample = all_cmds[:a.n]
        rows = {}
        for label, ref in (("base " + a.base, a.base), ("working tree", None)):
            tree = tree_of(ref, tmp) if ref else ROOT
            hits, errors = ratio(tree, all_cmds, repo)
            rows[label] = {"snapshot": f"{hits}/{len(all_cmds)} ({hits * 100 // max(1, len(all_cmds))}%)",
                           "exceptions": errors, **latency(tree, sample, repo)}
        print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
