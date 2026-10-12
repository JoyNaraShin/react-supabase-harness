---
type: 'regex'
target: 'trace'
weight: 2
pattern: '"name":"(?:Write|Edit|MultiEdit)","input":\{"file_path":"[^"]*supabase/migrations/\d{14}_[^"]*\.sql"[^\n]*private\.backfill_\w+'
---
