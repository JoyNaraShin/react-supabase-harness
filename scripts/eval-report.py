#!/usr/bin/env python3
"""eval 결과 집계 — `claude plugin eval --json` 결과 파일들에서 케이스별 Δ 와 pass^k 를 표로 낸다.

왜: 평균 점수만 보면 "가끔 막히는" 게이트와 "항상 막는" 게이트가 같아 보인다. 에이전트 신뢰도는
k 번 모두 성공했는가(pass^k)로 본다(Anthropic, Demystifying evals). Δ 는 같은 과제에서 플러그인을 켠
arm 과 끈 arm 의 평균 점수 차다.

사용:
  python3 scripts/eval-report.py evals/results/A.json [B.json …]   # 마크다운 표
  python3 scripts/eval-report.py --json …                           # 기계 판독
  python3 scripts/eval-report.py --min-pass-k 1.0 …                 # with arm pass^k 가 기준 미만이면 exit 1
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


def aggregate(paths: list) -> dict:
    acc = defaultdict(lambda: {"with": [], "without": [], "errors": 0})
    partial = []
    for p in paths:
        doc = json.load(open(p))
        if doc.get("partial"):
            partial.append(p)
        model_hint = None
        for part in p.replace("\\", "/").split("/")[-1].split("-"):
            if part.split(".")[0] in ("haiku", "sonnet", "opus"):
                model_hint = part.split(".")[0]
        for model, case, arm, r in runs_of(doc):
            model = model_hint if model == "default" and model_hint else model
            e = acc[(model, case)]
            if r.get("error") and "limit" in str(r.get("error")).lower():
                e["errors"] += 1          # 사용량 한도로 끊긴 런은 점수에서 뺀다(공식 문서 권고)
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
                     "passK_with": allpass(w), "passK_without": allpass(wo), "limitErrors": e["errors"]})
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
    ap.add_argument("--min-pass-k", type=float, default=None,
                    help="with arm 에서 pass^k 를 통과한 케이스 비율이 이 값 미만이면 exit 1")
    a = ap.parse_args()
    res = aggregate(a.files)
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
        lim = sum(r["limitErrors"] for r in rows)
        if lim:
            print(f"\n⚠ 사용량 한도로 끊긴 런 {lim}개는 점수에서 뺐다 — 재실행 필요")
    if a.min_pass_k is not None:
        pk = [r for r in rows if r["passK_with"] is not None]
        ratio = sum(r["passK_with"] for r in pk) / len(pk) if pk else 0
        if ratio < a.min_pass_k:
            print(f"pass^k 비율 {ratio:.2f} < {a.min_pass_k}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
