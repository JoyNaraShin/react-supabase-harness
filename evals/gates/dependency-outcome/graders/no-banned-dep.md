---
type: 'regex'
target: { source: file, path: package.json }
match: 'not_contains'
pattern: 'npm:antd'
weight: 2
---
