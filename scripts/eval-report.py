#!/usr/bin/env python3
"""eval 결과 집계 — `claude plugin eval --json` 결과 파일들에서 케이스별 Δ 와 pass^k 를 표로 낸다.

왜: 평균 점수만 보면 "가끔 막히는" 게이트와 "항상 막는" 게이트가 같아 보인다. 에이전트 신뢰도는
k 번 모두 성공했는가(pass^k)로 본다(Anthropic, Demystifying evals). Δ 는 같은 과제에서 플러그인을 켠
arm 과 끈 arm 의 평균 점수 차다.

사용:
  python3 scripts/eval-report.py evals/results/A.json [B.json …]   # 마크다운 표
  python3 scripts/eval-report.py --json …                           # 기계 판독
  python3 scripts/eval-report.py --min-pass-k 1.0 …
  python3 scripts/eval-report.py --latest evals/results/*-haiku.json  # 케이스마다 최근 결과만                 # with arm pass^k 가 기준 미만이면 exit 1
여러 파일을 주면 같은 (모델, 케이스)의 런을 합친다 — 나눠 돌린 배치를 한 표로 묶을 때.
"""
from __future__ import annotations
import argparse
import json
import sys
from collections import defaultdict


def runs_of(doc: dict):
    model = (doc.get("config") or {}).get("model") or doc.get("model") or "default"
    for c in doc.get("cases", []):
        for arm in ("with", "without"):
            for r in (c.get("arms") or {}).get(arm) or []:
                yield model, c["name"], arm, r


def latest_only(paths: list) -> dict:
    """(모델, 케이스)마다 가장 최근 결과 파일 하나만 남긴다 — 케이스 일부만 다시 돌린 뒤 지금 상태의 표를 낼 때.
    최근성은 파일의 runs startedAt 최댓값(없으면 파일 수정 시각)."""
    import os
    best = {}
    for p in paths:
        with open(p) as fh:
            doc = json.load(fh)
        for model, case, arm, r in runs_of(doc):
            t = r.get("startedAt") or str(os.path.getmtime(p))
            key = (_model_hint(p) if model == "default" else model, case)
            if key not in best or t > best[key][0]:
                best[key] = (t, p)
    return {k: v[1] for k, v in best.items()}


def _model_hint(p: str):
    hint = "default"
    for part in p.replace("\\", "/").split("/")[-1].split("-"):
        if part.split(".")[0] in ("haiku", "sonnet", "opus"):
            hint = part.split(".")[0]
    return hint


def aggregate(paths: list, latest: bool = False) -> dict:
    acc = defaultdict(lambda: {"with": [], "without": [], "errors": 0})
    partial = []
    keep = latest_only(paths) if latest else None
    for p in paths:
        with open(p) as fh:
            doc = json.load(fh)
        if doc.get("partial"):
            partial.append(p)
        model_hint = _model_hint(p)
        for model, case, arm, r in runs_of(doc):
            model = model_hint if model == "default" else model
            if keep is not None and keep.get((model, case)) != p:
                continue
            e = acc[(model, case)]
            err = str(r.get("error") or "")
            # 실행기 아티팩트: eval 실행기가 슬래시 프롬프트를 스킬로 펼치지 않은 런(모델이 Skill 도구로 부르려다
            # disable-model-invocation 으로 거절됨). 그 런은 스킬을 시험하지 않았다 — 점수가 아니라 측정 실패다.
            # 판정은 케이스의 `runner-` 접두 채점기(arm: with-only)가 한다.
            if not err and any(str(g.get("name", "")).startswith("runner-") and g.get("passed") is False
                               for g in r.get("graders") or []):
                err = "runner: 슬래시 프롬프트가 스킬로 펼쳐지지 않음"
            if err and "maximum number of turns" not in err:
                # 실행되지 못한 런(사용량 한도·샌드박스 거부·인증 실패 등)은 점수가 아니라 측정 실패다.
                # 세면 "아무것도 안 일어남"이 not_contains 채점기를 통과시켜 가짜 만점이 된다(실측: CI 126런).
                # 최대 턴 도달은 결과를 채점할 수 있으므로 남긴다.
                e["errors"] += 1
                e.setdefault("reasons", set()).add(err[:80])
                continue
            e[arm].append(r)
    rows = []
    for (model, case), e in sorted(acc.items()):
        w, wo = e["with"], e["without"]
        mean = lambda xs: sum(float(x.get("score") or 0) for x in xs) / len(xs) if xs else None
        allpass = lambda xs: (all(bool(x.get("passed")) for x in xs) if xs else None)
        mw, mwo = mean(w), mean(wo)
        rows.append({"model": model, "case": case, "k": len(w), "with": mw, "without": mwo,
                     "delta": (mw - mwo) if mw is not None and mwo is not None else None,
                     "passK_with": allpass(w), "passK_without": allpass(wo), "failedRuns": e["errors"],
                     "failReasons": sorted(e.get("reasons", ()))})
    return {"rows": rows, "partial": partial}


def fmt(x, signed=False):
    if x is None:
        return "–"
    if isinstance(x, bool):
        return "✓" if x else "✗"
    return (f"{x:+.2f}" if signed else f"{x:.2f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--latest", action="store_true", help="(모델, 케이스)마다 가장 최근 결과 파일만 쓴다")
    ap.add_argument("--min-pass-k", type=float, default=None,
                    help="with arm 에서 pass^k 를 통과한 케이스 비율이 이 값 미만이면 exit 1")
    a = ap.parse_args()
    res = aggregate(a.files, latest=a.latest)
    rows = res["rows"]
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print("| 모델 | 케이스 | k | with | without | Δ | pass^k with | pass^k without |")
        print("|---|---|---|---|---|---|---|---|")
        for r in rows:
            print(f"| {r['model']} | {r['case']} | {r['k']} | {fmt(r['with'])} | {fmt(r['without'])} | "
                  f"{fmt(r['delta'], True)} | {fmt(r['passK_with'])} | {fmt(r['passK_without'])} |")
        by = defaultdict(list)
        for r in rows:
            by[r["model"]].append(r)
        print()
        for m, rs in by.items():
            pk = [r for r in rs if r["passK_with"] is not None]
            ds = [r["delta"] for r in rs if r["delta"] is not None]
            print(f"- {m}: 케이스 {len(rs)} · pass^k(with) {sum(r['passK_with'] for r in pk)}/{len(pk)} · "
                  f"평균 Δ {sum(ds) / len(ds):+.2f}" if ds else f"- {m}: 케이스 {len(rs)}")
        if res["partial"]:
            print(f"\n⚠ partial 결과(비용 상한·중단) 포함: {', '.join(res['partial'])} — 추세에서 뺄 것")
        lim = sum(r["failedRuns"] for r in rows)
        if lim:
            reasons = sorted({x for r in rows for x in r["failReasons"]})
            print(f"\n⚠ 실행되지 못한 런 {lim}개는 점수에서 뺐다 — 재실행 필요. 사유: " + " / ".join(reasons[:3]))
    if a.min_pass_k is not None:
        empty = [r["case"] for r in rows if r["passK_with"] is None]
        if empty:   # 유효한 런이 하나도 없는 케이스가 있으면 통과로 치지 않는다
            print(f"측정 실패 케이스 {len(empty)}개: {', '.join(empty[:5])}", file=sys.stderr)
            return 1
        pk = [r for r in rows if r["passK_with"] is not None]
        ratio = sum(r["passK_with"] for r in pk) / len(pk) if pk else 0
        if ratio < a.min_pass_k:
            print(f"pass^k 비율 {ratio:.2f} < {a.min_pass_k}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
