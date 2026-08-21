# DSH schema 指纹(版本标签:0.1.0-rc.6)

- 会话样本: 1 个
- JSONL 行数: 24972

## 顶层事件类型(type)

| type | 次数 |
|---|---|
| agent/inbox/spliced | 280 |
| approval/policy | 2 |
| assistant/chunk | 5774 |
| assistant/message | 579 |
| command/done | 3 |
| command/run | 3 |
| compaction/end | 2 |
| compaction/start | 2 |
| compaction/summary | 2 |
| llm/retry | 2 |
| llm/retry-started | 2 |
| permission/preset | 2 |
| reasoning-chunks | 7527 |
| request/context | 2 |
| request/header | 5 |
| sandbox/mode | 2 |
| session | 1 |
| session/end-seed | 1 |
| session/title | 2 |
| session/title-llm-request | 1 |
| step/end | 585 |
| step/start | 585 |
| text-chunks | 3852 |
| todo/write | 11 |
| tool-call-chunks | 4108 |
| tool-workflow/agent-end | 2 |
| tool-workflow/agent-start | 2 |
| tool-workflow/run-end | 2 |
| tool-workflow/run-start | 2 |
| tool/call | 576 |
| tool/result | 576 |
| turn/end | 118 |
| turn/start | 118 |
| user/message | 144 |
| web/deepseek-search-llm-request | 97 |

## assistant/chunk 子类型(chunk.type)

- block-end: 1399
- block-start: 1403
- finish: 582
- reasoning-delta: 765
- text-delta: 452
- tool-call-delta: 591
- usage: 582

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
