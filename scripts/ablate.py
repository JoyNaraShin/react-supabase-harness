#!/usr/bin/env python3
"""훅별 ablation — 훅 하나를 뺀 플러그인 사본으로 관련 eval 케이스를 돌려, 전체 플러그인 대비 점수 차를 낸다.

왜: `claude plugin eval` 의 ablation 은 플러그인 전체를 켜고 끄는 것뿐이다(--ablation none | with-without).
"이 훅이 모델 혼자 못 하는 것을 더하는가"(Anthropic, Harness design)는 구성요소 단위로 봐야 한다 —
빼도 점수가 그대로면 그 훅은 그 케이스에서 낡은 비계다(제거 후보, 결정은 사람).

방식: 레포를 임시 디렉터리로 복사 → hooks.json 에서 그 훅 항목만 삭제 → 같은 케이스를 --ablation none 으로
실행. 기준(전체) arm 도 같은 배치에서 함께 돈다. 모델 호출 비용(플랜 사용량 또는 API 과금)이 든다.

사용:
  python3 scripts/ablate.py --model haiku --runs 3                 # 기본 매핑 전부
  python3 scripts/ablate.py --hooks gate-engine snapshot-guard     # 일부 훅만
  python3 scripts/ablate.py --dry-run                              # 무엇을 돌릴지만 출력
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 훅 → 그 훅이 지키는 케이스(글롭). 케이스 이름은 evals/<그룹>/<이름> → "<그룹>-<이름>".
DEFAULT_MAP = {
    # verify FAIL 위 커밋은 block-destructive-git(verify_failed)이 막는다. 그 케이스는 판정 기록을 미리 깔고
    # 시작하므로 기록을 쓰는 subagent-audit 은 관여하지 않는다 — 기록 쓰기는 계약 테스트가 맡는다.
    "block-destructive-git": ["destructive-*", "commit-unapproved-commit", "commit-verify-fail-blocks"],
    "gate-engine": ["gates-*"],
    "block-impl-delegation": ["delegation-impl-to-subagent"],
    "snapshot-guard": ["snapshot-*"],
    "report-failures-gate": ["stop-*"],
    "require-agent-output-file": ["delegation-review-to-subagent"],
}


def variant(hook: str | None) -> Path:
    d = Path(tempfile.mkdtemp(prefix=f"ablate-{hook or 'full'}-"))
    shutil.copytree(ROOT, d / "plugin", ignore=shutil.ignore_patterns(".git", "results", "__pycache__", "backups"))
    if hook:
        hj = d / "plugin" / "hooks" / "hooks.json"
        data = json.loads(hj.read_text())
        for ev in list(data["hooks"]):
            groups = []
            for g in data["hooks"][ev]:
                g["hooks"] = [h for h in g["hooks"] if f"/hooks/{hook}." not in h["command"]]
                if g["hooks"]:
                    groups.append(g)
            data["hooks"][ev] = groups
        hj.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return d / "plugin"


def run(plugin: Path, case: str, model: str, runs: int, out: Path) -> dict:
    cmd = ["claude", "plugin", "eval", str(plugin), "--scaffold", "--trust-plugin", "--no-publish",
           "--allow-tools", "Bash", "Write", "Edit", "Agent", "--runs", str(runs), "--ablation", "none",
           "--model", model, "--case", case, "--json", str(out)]
    subprocess.run(cmd, capture_output=True, text=True)
    try:
        doc = json.load(open(out))
    except Exception:
        return {}
    # 오류로 끝난 런(사용량 한도·타임아웃 등)은 점수 0 이 아니라 측정 실패다 — 빼고, 남은 런이 없으면 None.
    # 최대 턴 도달은 결과를 채점할 수 있으므로 남긴다.
    out = {}
    for c in doc.get("cases", []):
        ok = [r for r in c["arms"]["with"] if not r.get("error") or "maximum number of turns" in str(r["error"])]
        out[c["name"]] = [float(r.get("score") or 0) for r in ok]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hooks", nargs="*", default=list(DEFAULT_MAP))
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    plan = [(h, c) for h in a.hooks for c in DEFAULT_MAP.get(h, [])]
    if a.dry_run:
        for h, c in plan:
            print(f"{h:28s} {c}")
        return 0
    stamp = time.strftime("%Y%m%dT%H%M%S")
    outdir = ROOT / "evals" / "results" / f"ablate-{stamp}-{a.model}"
    outdir.mkdir(parents=True, exist_ok=True)
    full = variant(None)
    cache_full: dict = {}
    rows = []
    for h, c in plan:
        if c not in cache_full:
            cache_full[c] = run(full, c, a.model, a.runs, outdir / f"full-{c.replace('*', 'x')}.json")
        v = variant(h)
        cut = run(v, c, a.model, a.runs, outdir / f"no-{h}-{c.replace('*', 'x')}.json")
        shutil.rmtree(v.parent, ignore_errors=True)
        for name, scores in cache_full[c].items():
            fs = sum(scores) / len(scores) if scores else None
            cs = cut.get(name) or []
            cm = sum(cs) / len(cs) if cs else None
            rows.append({"hook": h, "case": name, "full": fs, "without_hook": cm,
                         "contribution": (fs - cm) if fs is not None and cm is not None else None})
    shutil.rmtree(full.parent, ignore_errors=True)
    (outdir / "ablation.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    print("| 훅 | 케이스 | 전체 | 훅 제거 | 기여 |")
    print("|---|---|---|---|---|")
    for r in rows:
        f = lambda x, s=False: "측정 실패" if x is None else (f"{x:+.2f}" if s else f"{x:.2f}")
        print(f"| {r['hook']} | {r['case']} | {f(r['full'])} | {f(r['without_hook'])} | {f(r['contribution'], True)} |")
    failed = sum(1 for r in rows if r["contribution"] is None)
    if failed:
        print(f"\n⚠ {failed}행은 유효한 런이 없다(사용량 한도·오류) — 한도가 풀린 뒤 `--hooks` 로 그 훅만 다시 돌린다")
    print(f"\n결과: {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
