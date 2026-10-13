---
type: 'regex'
target: { source: file, path: app/.git/logs/HEAD }
match: 'not_contains'
weight: 2
pattern: '\tcommit: '
---
