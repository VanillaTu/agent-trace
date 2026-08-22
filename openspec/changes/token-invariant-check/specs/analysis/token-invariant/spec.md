# Spec — analysis/token-invariant (A1)

## Purpose

为 AgentTrace 增加一个**分析层会话级数据块** `TokenInvariant`,检测 harness 的「Token 记账双写不变量」:DSH 每个 (turn,step) 的 usage 在 `assistant/chunk{type:usage}` 与 `assistant/message` 各写一次。数据块统计双写范围与"非去重消费方的假设性溢出上界",并给出 hedged 去重建议。它挂在 `DiagnosisResult.token_invariant`,与 `ContextHealth` / `profile` 同构,仅在 `enable_analysis=True` 时生成并渲染。**只做观测 + 风险,causal_claim=NONE,不判 harness bug,不混算 wasted。**

## ADDED Requirements

### Requirement: Adapter 生成双写观测事件

adapter 解析时 SHALL 按 (turn,step) 收集 `[(source, usage_dict)]`(source = "chunk" / "message");对 ≥2 来源的项,SHALL 在解析末尾追加源保真观测事件:

- 所有来源 all-pairs 数值一致(比较 `inputTokens`/`outputTokens`/`cacheReadTokens`/`reasoningTokens`)SHALL 生成 `token/usage-duplicate`,data 含 `source_count` / `total_tokens`(= 一份 `inputTokens + outputTokens`)/ `sources`。
- 任一来源数值不一致 SHALL 生成 `token/usage-inconsistent`,data 含 `source_count` / `sources`。
- 单来源(仅 chunk 或仅 message)SHALL NOT 生成任何双写事件(不误报)。
- 事件生成 SHALL 只追加观测,不改变哪份 usage 获胜、不改遍历/去重顺序、不改 `Step.usage` 赋值。

#### Scenario: 双写数值一致

- **WHEN** 同一 (turn,step) 的 chunk usage 与 message usage 数值一致
- **THEN** trace.events 含一条 `token/usage-duplicate`,其 `data.total_tokens` = 该 step 的 `inputTokens + outputTokens`。

#### Scenario: 双写数值不一致

- **WHEN** 同一 (turn,step) 的 chunk usage 与 message usage 数值不一致
- **THEN** trace.events 含一条 `token/usage-inconsistent`,不含 `token/usage-duplicate`。

#### Scenario: 单来源不报

- **WHEN** 某 (turn,step) 仅有 chunk usage、无 message usage(或反之)
- **THEN** trace.events 不新增任何 `token/usage-*` 事件。

### Requirement: TokenInvariant 数据块计算

分析层 SHALL 提供 `build_token_invariant(trace)` 纯函数,从 `trace.events[]` 读取双写事件,确定性计算:

- `duplicate_usage_steps` = `token/usage-duplicate` 事件数;
- `naive_double_count_tokens` = 各 duplicate 事件 `data.total_tokens` 之和(仅含数值一致的 step,不一致被排除 → 该值为下界而非上界);
- `total_deduped_tokens` = 会话所有 step 的 `Step.usage.total_tokens()` 合计;
- `over_count_factor` = `(total_deduped + naive_double) / total_deduped`,无双写=1.0、全双写=2.0、部分双写 ∈ (1.0, 2.0);`total_deduped == 0` 时 SHALL = 1.0(不抛除零异常);
- `double_write_multiplier` = 恒 2.0(双写子集内乘数,不被全局分母稀释);
- `inconsistent_usage_steps` = `token/usage-inconsistent` 事件数(独立,不参与溢出计算);
- `dedup_required` = 双写子集存在(`duplicate_usage_steps > 0`)⇒ True,否则 False(hedged 推荐,非断言)。

#### Scenario: 空会话 / 无双写

- **WHEN** trace 无 `token/usage-duplicate` 事件
- **THEN** `duplicate_usage_steps=0`、`naive_double_count_tokens=0`、`over_count_factor=1.0`、`dedup_required=False`,不虚构数值。

#### Scenario: 全双写

- **WHEN** 会话每个 step 都有数值一致的 duplicate 事件
- **THEN** `over_count_factor == 2.0`,且 `double_write_multiplier == 2.0`。

#### Scenario: 除零保护

- **WHEN** 会话所有 step 的 usage 均为 0 且存在 duplicate 事件
- **THEN** `over_count_factor == 1.0`,不抛异常。

### Requirement: 分析层门控与 additive

`TokenInvariant` SHALL 仅在 `enable_analysis=True` 时生成与渲染;默认(关闭)SHALL 不产生任何新输出,与 v0.5 逐字节一致。SHALL NOT 注册进 `ALL_DETECTORS` / `ALL_ATTRIBUTION_ENGINES`;SHALL NOT 进入 findings / attributions;SHALL NOT 改变现有 detector 的检测行为。

#### Scenario: 默认关闭零影响

- **WHEN** 未开分析层(`enable_analysis=False`)运行完整 pipeline
- **THEN** `DiagnosisResult.token_invariant` = `None`,报告输出与 v0.5 逐字节一致。

#### Scenario: 不进入检测/归因体系

- **WHEN** 开启分析层
- **THEN** `ALL_DETECTORS` / `ALL_ATTRIBUTION_ENGINES` 数量不变;attributions 不含 token-invariant 条目(无成本归因)。

### Requirement: 报告渲染与语义边界

`enable_analysis=True` 时,报告 SHALL 在上下文健康度块后追加「架构不变量检查 — Token 记账」块,含:双写观测、去重后会话总量、非去重消费方的假设性溢出上界、双写子集内乘数(精确 2×,先报)、全局稀释后溢出倍数(2 位小数,后报)、不一致步数(若有)、去重建议。报告 SHALL NOT 出现 "wasted" / "Total wasted" / "harness bug" 措辞;SHALL 陈述观测 + 风险,causal_claim=NONE。

#### Scenario: 有双写时渲染

- **WHEN** 开启分析层且存在双写
- **THEN** 报告含「架构不变量检查」块,先陈述"双写 step 精确 2× 高估",再陈述"全局稀释后溢出倍数";块内无 "wasted" / 因果断言。

#### Scenario: 无双写时渲染

- **WHEN** 开启分析层且无双写
- **THEN** 报告含「架构不变量检查」块,显示"未检测到 usage 双写";若存在不一致步数则单独提示源保真度异常。
