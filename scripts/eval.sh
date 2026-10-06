#!/usr/bin/env bash
# 하네스 eval 실행 — with(플러그인) / without(기준선) 두 arm 의 점수 차(Δ)를 낸다.
# 모델 비용이 들고 사용자 플랜 사용량을 쓴다. PR 마다 돌리지 않는다.
#
#   scripts/eval.sh                          # 세션 기본 모델, 케이스당 3런
#   MODEL=haiku RUNS=1 scripts/eval.sh       # 하위 모델 축
#   CASE=destructive-force-push scripts/eval.sh
#   TAG=positive scripts/eval.sh             # 과차단 검사만
#   MAX_COST=5 scripts/eval.sh               # 비용 상한(USD) — 넘으면 부분 결과로 종료(exit 2)
set -euo pipefail
cd "$(dirname "$0")/.."

args=(plugin eval . --scaffold --trust-plugin --no-publish
      --allow-tools Bash Write Edit Agent
      --runs "${RUNS:-3}" --threshold "${THRESHOLD:-0}")
[ -n "${MODEL:-}" ]    && args+=(--model "$MODEL")
[ -n "${CASE:-}" ]     && args+=(--case "$CASE")
[ -n "${TAG:-}" ]      && args+=(--tag "$TAG")
[ -n "${MAX_COST:-}" ] && args+=(--max-cost-usd "$MAX_COST")
[ -n "${KEEP:-}" ]     && args+=(--keep-temp)

mkdir -p evals/results
out="evals/results/$(date -u +%Y%m%dT%H%M%SZ)-${MODEL:-default}.json"
claude "${args[@]}" --json "$out"
echo "결과: $out"
