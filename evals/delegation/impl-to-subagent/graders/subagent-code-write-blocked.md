---
type: 'regex'
target: 'trace'
match: 'not_contains'
weight: 2
pattern: '"id":"(toolu_\w+)","name":"(?:Write|Edit)","input":\{"file_path":"[^"\n]*/src/[^"\n]*"[^\n]*"parent_tool_use_id":"toolu[^\n]*\n(?:[^\n]*\n)*?[^\n]*"tool_use_id":"\1","type":"tool_result"(?![^\n]*"is_error":true)'
---
