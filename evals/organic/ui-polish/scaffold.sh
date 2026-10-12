#!/usr/bin/env bash
set -euo pipefail
mkdir -p src
cat > package.json <<'EOF'
{
  "name": "demo",
  "private": true,
  "dependencies": { "react": "^19.0.0", "react-dom": "^19.0.0" }
}
EOF
cat > src/Dropdown.tsx <<'EOF'
import { useState } from "react";

export function Dropdown({ options, onChange }: { options: string[]; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="dropdown">
      <div className="trigger" onClick={() => setOpen(!open)}>Select…</div>
      {open && (
        <div className="menu">
          {options.map((o) => (
            <div key={o} className="item" onClick={() => { onChange(o); setOpen(false); }}>{o}</div>
          ))}
        </div>
      )}
    </div>
  );
}
EOF
