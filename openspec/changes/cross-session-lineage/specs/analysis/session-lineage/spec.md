# Spec — analysis/session-lineage (A2)

## Purpose

为 AgentTrace 增加一个**分析层会话级数据块** `SessionLineage`,观测跨会话血缘:沿 `header.parentSession` 权威边,把一个会话的子会话(SUBAGENT / FORKED_SESSION)的 token 规模/工具计数/detector 信号**聚合回父会话**(聚合观测值,非成本)。它挂在 `DiagnosisResult.session_lineage`,与 `ContextHealth` / `TokenInvariant` 同构,仅在 `enable_analysis=True` 且传入 `session_map` 时生成并渲染。**只报观测 + 边界,causal_claim=NONE,不混算 wasted,子会话 token 只归自己、禁止沿链再加总。**

## ADDED Requirements

### Requirement: Adapter 提取 lineage 头字段

adapter 解析 session 事件时 SHALL 在 `trace.metadata` 中记录 `parentSession` / `origin` / `delegationDepth`(来自会话事件**顶层**,非 `data` 内)。此扩展为 additive,不改既有 `cwd` / `agentPreset` 提取,不改 `Step` / `trace.events` / 遍历顺序。

#### Scenario: 解析 session 头 lineage 字段

- **WHEN** 解析一个含 `parentSession` / `origin` / `delegationDepth` 的 session 事件
- **THEN** `trace.metadata["parentSession"]` / `["origin"]` / `["delegationDepth"]` 分别等于对应顶层字段。

### Requirement: SessionLineage 数据块计算

分析层 SHALL 提供 `build_session_lineage(session_id, session_map, visited=None)` 纯函数,沿 `header.parentSession` 权威边递归聚合:

- `own_tokens` / `own_steps` / `own_tools`:该会话自身(权威,可全局加总,不被任何祖先改写)。
- SUBAGENT 子代(origin=="subagent")递归聚合进 `lineage_descendant_*`;FORKED_SESSION 子代(origin 为空但 parentSession 有值)递归聚合进 `lineage_fork_descendant_*`(独立层,默认不混算)。
- 子代遍历按 session_id 排序保证确定性;`visited` 防环(树形安全网)。
- **防重复计数**:每个节点自存子树和,后代只计一次;禁止沿链再加总祖先的子树和。
- `parent_category`:"subagent" | "forked_session" | "none"(无父)。
- `lineage_depth`:沿 parentSession 链从根的结构化推导(不用 delegationDepth)。
- `unresolvable_edges` / `is_resolvable_subgraph`:父指向不在 `session_map` 的会话时,记不可解析且 `is_resolvable_subgraph=False`(不猜、不伪造父子关系)。

#### Scenario: 空 / 单会话

- **WHEN** 会话无父无子
- **THEN** `own_*` 有值、`lineage_descendant_*` 全零、`child_count=0`、`parent_category="none"`。

#### Scenario: 嵌套聚合防重复

- **WHEN** 根→子→孙三层 subagent 链
- **THEN** 根的 `lineage_descendant_tokens` = 子.own + 孙.own(各出现一次);孙的 own 不沿链重复加总。

#### Scenario: 不可解析父

- **WHEN** 会话 `parentSession` 指向不在 `session_map` 的会话
- **THEN** `unresolvable_edges>0`、`is_resolvable_subgraph=False`,不抛异常。

### Requirement: 权威父子来源与两类子代区分

SHALL 只以 `header.parentSession` 为权威父子来源;`tool-workflow/agent-start.childId` 与 `senderSessionId` SHALL NOT 作为 lineage 边。SHALL 区分两类子代:SUBAGENT(origin=="subagent",进主聚合)与 FORKED_SESSION(origin 为空但 parentSession 有值,进独立 fork 层);未知 origin 保守按 FORKED_SESSION。fork 子代 SHALL NOT 做 token 互斥/去重。

#### Scenario: agent-start 不作边

- **WHEN** 会话含 `tool-workflow/agent-start(childId=X)` 事件,但 X 的 `parentSession` 不是该会话
- **THEN** X 不作为该会话的子代(不产生 lineage 边)。

#### Scenario: fork 分离不混算

- **WHEN** 父同时有 SUBAGENT 子与 FORKED_SESSION 子
- **THEN** fork 子的 token 只进 `lineage_fork_descendant_*`,不进 `lineage_descendant_*`。

### Requirement: 分析层门控与 additive

`SessionLineage` SHALL 仅在 `enable_analysis=True` 且传入 `session_map` 时生成;默认/未传 `session_map` SHALL 不产生任何新输出,与 v0.5 逐字节一致。SHALL NOT 注册进 `ALL_DETECTORS` / `ALL_ATTRIBUTION_ENGINES`;SHALL NOT 进入 findings / attributions;SHALL NOT 改变现有 detector 的检测行为。

#### Scenario: 默认关闭零影响

- **WHEN** `enable_analysis=False` 运行完整 pipeline
- **THEN** `DiagnosisResult.session_lineage` = `None`,报告输出与 v0.5 逐字节一致。

#### Scenario: 不进入检测/归因体系

- **WHEN** 开启分析层
- **THEN** `ALL_DETECTORS` / `ALL_ATTRIBUTION_ENGINES` 数量不变;attributions 不含 session-lineage 条目。

### Requirement: 报告渲染与语义边界

`enable_analysis=True` 且 `session_lineage is not None` 时,报告 SHALL 渲染「跨会话 Lineage(A2)」块,含自身指标、父会话/父边类别、图深度、subagent 子代规模观测、forked-session 延续链(仅 `fork_session_child_count>0`)、形态(仅 subagent 有值)、边界标注。报告 SHALL NOT 出现 "wasted" / "Total wasted" / 因果断言;后代 token 标注"token 规模观测,非成本";边界标注"本机可解析子图内成立"。

#### Scenario: 渲染 lineage 块

- **WHEN** 开启分析层且传入 session_lineage
- **THEN** 报告含「跨会话 Lineage」块,后代 token 标注"token 规模观测,非成本",无 "wasted" / 因果断言。

#### Scenario: 边界标注

- **WHEN** 渲染 lineage 块
- **THEN** 块内含"本机可解析子图内成立",并给出覆盖会话数与不可解析边数(若有)。
