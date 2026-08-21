## Why

Dogfooding 发现:分析当前会话(<session-id>)时,一次真实的"工具调用缺参数 → 报错 → 重试成功"(send_session_message 缺 text)事件,**5 个 detector 全部没检出**——TOOL-001 因参数不完全一致走中间档不判重复,RETRY-001 只管 llm/retry 不管工具失败重试。这暴露了检测盲区,且该模式正是最初设计(05 文档 L558:invalid param retry)里定义过但未实现的缺陷类别。需要**系统性排查**全部盲区,而不是零散发现。

## What Changes

- **系统性盲区审计**:遍历本机真实 DSH 会话(开发会话/子代理会话/当前会话/历史分析过的会话),逐个跑 `agenttrace analyze`,人工核对 trace 中的异常事件,找出"发生了但 5 个 detector 没检出"的模式。
- **产出《检测盲区清单》**:每个盲区 = 真实 trace 样本(会话 id + 事件位置)+ 事件类型 + 对应最初设计的缺陷类别(duplicate / retry storm / invalid param retry / oversized observation / low-value unused observation / 其他)+ 建议的新 detector ID(TOOL-004 等)。
- **已确认第 1 个盲区(种子样本)**:
  - 样本:`<session-id>`(当前会话,send_session_message 缺 text 报错 → 重试成功)
  - 事件类型:工具调用参数错误(invalid arguments)→ 失败 → 手动重试成功
  - 对应类别:invalid param retry
  - 建议 detector:TOOL-004 invalid-param-retry
- 盲区清单作为未来 detector 的 backlog;开源后转 GitHub issues。

## Capabilities

### New Capabilities

- 无(本 change 为调研/审计型,产出文档,`skip_specs: true`)。

### Modified Capabilities

- 无。

## Impact

- **新增文档**:`research/detector-blindspot-audit/blindspot-inventory.md`(盲区清单,可追加)。
- **依赖**:真实会话样本(`~/.dsh/sessions/--D-workspace--/` 下),`agenttrace analyze` CLI。
- 不触碰 `agenttrace/`、`tests/` 代码;不改变任何行为。
