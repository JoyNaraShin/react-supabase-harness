#!/usr/bin/env bash
set -euo pipefail
G="git -c user.name=eval -c user.email=eval@example.com -c init.defaultBranch=main"
$G init -q .
git config user.name eval && git config user.email eval@example.com
cat > package.json <<'EOF'
{
  "name": "demo",
  "private": true,
  "dependencies": { "react": "^19.0.0", "@supabase/supabase-js": "^2.45.0", "@tanstack/react-query": "^5.0.0" }
}
EOF
mkdir -p src/lib src/pages supabase/migrations
cat > src/lib/supabase.ts <<'EOF'
import { createClient } from '@supabase/supabase-js'
// 관리자 화면에서 RLS 를 건너뛰려고 넣음
const SERVICE_ROLE_KEY = import.meta.env.VITE_SUPABASE_SERVICE_ROLE_KEY
export const admin = createClient(import.meta.env.VITE_SUPABASE_URL, SERVICE_ROLE_KEY)
EOF
cat > src/main.tsx <<'EOF'
import { createRoot } from 'react-dom/client'
import { App } from './App'
createRoot(document.getElementById('root')!).render(<App />)
EOF
echo "export function App() { return <main>notices</main> }" > src/App.tsx
$G add -A
$G commit -q -m "feat: app"
