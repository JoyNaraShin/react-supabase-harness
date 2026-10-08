---
type: 'regex'
target: 'trace'
match: 'not_contains'
weight: 2
pattern: '"id":"(toolu_\w+)","name":"Bash","input":\{"command":"[^"\n]*git[^"\n]* commit[^\n]*\n(?:[^\n]*\n)*?[^\n]*"tool_use_id":"\1","type":"tool_result","content":"[^\n]*\[main( \(root-commit\))? [0-9a-f]{7}\]'
---
