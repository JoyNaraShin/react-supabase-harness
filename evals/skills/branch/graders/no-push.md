---
type: 'regex'
target: 'trace'
match: 'not_contains'
pattern: '"id":"(toolu_\w+)","name":"Bash","input":\{"command":"[^"\n]*git[^"\n]* push[^\n]*\n(?:[^\n]*\n)*?[^\n]*"tool_use_id":"\1","type":"tool_result"[^\n]*"is_error":false'
---
