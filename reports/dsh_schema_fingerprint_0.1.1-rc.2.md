---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 8081f328900ef2b72a0ac862df432635_8797a56c9d8511f1a54f525400f8a581
    ReservedCode1: 0JMPXvDv6E7gkYzW9+Xuzphgh93NBrcnf92x2tw2H0kxohGp0itPYC7x8vt9puC9X45ue3T3Tk9F7B9RFS6q9IyH9zBmZ6u1cux3QWKnEI2AwyZWbUuEkYlhrecsetARrH/bY9wys6pxC2ws7rgMXV4NGoB9Zy11pyoE/E49k6NAg9r9wJsgRvBcY9s=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 8081f328900ef2b72a0ac862df432635_8797a56c9d8511f1a54f525400f8a581
    ReservedCode2: 0JMPXvDv6E7gkYzW9+Xuzphgh93NBrcnf92x2tw2H0kxohGp0itPYC7x8vt9puC9X45ue3T3Tk9F7B9RFS6q9IyH9zBmZ6u1cux3QWKnEI2AwyZWbUuEkYlhrecsetARrH/bY9wys6pxC2ws7rgMXV4NGoB9Zy11pyoE/E49k6NAg9r9wJsgRvBcY9s=
---

# DSH schema 指纹(版本标签:0.1.1-rc.2)

- 会话样本: 1 个
- JSONL 行数: 9195

## 顶层事件类型(type)

| type | 次数 |
|---|---|
| agent/inbox/spliced | 285 |
| approval/policy | 2 |
| assistant/chunk | 4018 |
| assistant/message | 584 |
| command/done | 3 |
| command/run | 3 |
| compaction/end | 2 |
| compaction/start | 2 |
| compaction/summary | 2 |
| llm/retry | 2 |
| llm/retry-started | 2 |
| permission/preset | 2 |
| reasoning-chunks | 373 |
| request/context | 2 |
| request/header | 6 |
| sandbox/mode | 2 |
| session | 1 |
| session/end-seed | 3 |
| session/title | 3 |
| session/title-llm-request | 1 |
| step/end | 591 |
| step/start | 591 |
| text-chunks | 469 |
| todo/write | 11 |
| tool-call-chunks | 581 |
| tool-workflow/agent-end | 2 |
| tool-workflow/agent-start | 2 |
| tool-workflow/run-end | 2 |
| tool-workflow/run-start | 2 |
| tool/call | 581 |
| tool/result | 581 |
| turn/end | 120 |
| turn/start | 120 |
| user/message | 147 |
| web/deepseek-search-llm-request | 97 |

## assistant/chunk 子类型(chunk.type)

- block-end: 1414
- block-start: 1418
- finish: 587
- tool-call-delta: 12
- usage: 587

## usage 字段

- `cacheReadTokens`
- `inputTokens`
- `outputTokens`
- `reasoningTokens`

## session 行顶层键

- `agentPreset`
- `createdAt`
- `cwd`
- `delegationDepth`
- `id`
- `parentSession`
- `seedLength`
- `type`
- `version`

## tool/call data 键

- `arguments`
- `callId`
- `name`
- `step`
- `turn`

## tool/result data 键

- `error`
- `message`
- `meta`
- `step`
- `turn`
*（内容由AI生成，仅供参考）*
