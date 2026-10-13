#!/usr/bin/env bash
set -euo pipefail
mkdir -p src
cat > package.json <<'EOF'
{
  "name": "demo",
  "private": true,
  "dependencies": { "react": "^19.0.0" }
}
EOF
echo "export const a = 1" > src/main.ts
mkdir -p notes
printf 'export function sum(xs: number[]) {\n  let t = 0\n  for (const x of xs) t += x\n  return t\n}\n' > src/sum.ts
# 하네스 탑승 마커 — 위임 차단·파일 산출 강제는 T1(관할 프로젝트만) 훅이다(0.37.0). 없으면 이 케이스는 아무것도 재지 않는다.
printf '{"role":"project"}\n' > .harness.json
