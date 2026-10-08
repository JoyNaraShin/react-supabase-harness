#!/usr/bin/env bash
set -euo pipefail
cat > package.json <<'EOF'
{
  "name": "demo",
  "private": true,
  "dependencies": { "react": "^19.0.0", "@supabase/supabase-js": "^2.45.0", "@tanstack/react-query": "^5.0.0" }
}
EOF
mkdir -p src/routes src/lib docs/plans
printf "# Phase 1\\n\\n## Story 3: 주문 목록\\nacceptance: 로그인한 사용자가 자기 주문을 본다\\n" > docs/plans/phase-1-orders.md
echo "export const a = 1" > src/main.ts
mkdir -p src/lib
cat > src/lib/supabase.ts <<'EOF2'
import { createClient, type SupabaseClient } from '@supabase/supabase-js'
let client: SupabaseClient | null = null
export function getSupabase(): SupabaseClient {
  client ??= createClient(import.meta.env.VITE_SUPABASE_URL, import.meta.env.VITE_SUPABASE_ANON_KEY)
  return client
}
EOF2
