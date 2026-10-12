---
type: 'regex'
target: { source: file, path: package.json }
match: 'not_contains'
weight: 2
pattern: 'radix|headlessui|@mui|antd|chakra|shadcn'
---
