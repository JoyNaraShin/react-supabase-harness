---
type: 'regex'
target: { source: file, path: .git/logs/HEAD }
match: 'not_contains'
weight: 2
pattern: '\tcommit: '
---
