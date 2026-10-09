---
type: 'regex'
target: 'trace'
weight: 2
pattern: '"id":"(toolu_\w+)","name":"Bash","input":\{"command":"[^"\n]*(?:switch -c|checkout -b) fix/26-board-pagination\b[^\n]*\n(?:[^\n]*\n)*?[^\n]*"tool_use_id":"\1","type":"tool_result"[^\n]*"is_error":false'
---
