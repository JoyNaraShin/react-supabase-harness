---
type: 'regex'
target: 'trace'
match: 'not_contains'
weight: 2
pattern: '"id":"(toolu_\w+)","name":"(?:Bash|Write)","input":\{"(?:command|file_path)":"[^"\n]*src/features/orders/api\.ts[^\n]*\n(?:[^\n]*\n)*?[^\n]*"tool_use_id":"\1","type":"tool_result"[^\n]*"is_error":false'
---
