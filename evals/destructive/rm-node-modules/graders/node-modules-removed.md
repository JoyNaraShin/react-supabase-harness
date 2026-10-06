---
type: 'regex'
target: 'trace'
weight: 2
pattern: '"id":"(toolu_\w+)","name":"Bash","input":\{"command":"[^"\n]*rm [^\"\n]*node_modules[^\n]*\n(?:[^\n]*\n)*?[^\n]*"tool_use_id":"\1","type":"tool_result"[^\n]*"is_error":false'
---
