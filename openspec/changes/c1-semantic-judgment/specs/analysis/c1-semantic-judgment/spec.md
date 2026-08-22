# Spec — c1-semantic-judgment (C1)

## Purpose

为 AgentTrace 增加一个**语义判断候选清单层**:把已检出的、需要语义判断的候选重复(尤其 `semantic=debated` 轮询型工具)以**适合 agent 一次性分析的结构化形式**暴露——每个候选附需判断的上下文(前后 step、工具结果、状态变化)。**调用 AgentTrace 的 agent**(DSH harness 的 agent,本身是 LLM)读取候选清单后,用自身模型判定每个候选"真冗余 / 合法 + 置信度 + 理由",回填进 AgentTrace 报告。**LLM 语义层在 agent 身上,不在 AgentTrace 进程内**;AgentTrace 保持确定性工具、不内置 LLM 调用、不做 DSH 插件。

## ADDED Requirements

### Requirement: 候选语义判断清单生成

分析层 SHALL 从已检出的 TOOL-001/TOOL-004 finding 中,生成"需语义判断的候选重复清单"。每个候选条目包含:
- `finding 定位`(rule_id / turn_id / step_id / fingerprint)。
- `工具与参数`(tool_name / arguments)。
- `是否 debated`(轮询型工具与否)。
- **判断上下文**:该重复调用前后关键上下文——前一次调用与本次调用之间是否有**会改变 agent 状态的动作**(如写入/工具结果变化),以帮助 agent 判定"真冗余/合法"。

#### Scenario: 生成候选清单

- **WHEN** 一个会话含 TOOL-001 重复 finding(含 debated 轮询型)
- **THEN** 生成候选清单,每个候选附工具/参数/是否 debated/判断上下文。

### Requirement: 语义优先输出轮询型候选

分析层 SHALL 在候选清单中**优先/显著标出 `semantic=debated`(轮询型)候选**——这些是规则层无法区分、最需要 agent 语义判断的部分。

#### Scenario: 轮询型置前

- **WHEN** 候选清单含确定性重复与轮询型重复
- **THEN** 轮询型(debated)候选被显著标出,供 agent 优先审视。

### Requirement: 语义判断回填合并

分析层 SHALL 支持 agent 的 LLM 判定**回填**(每个候选的 `verdict` + `confidence` + `reason`),并在报告中合并显示。回填的 `verdict.source` SHALL 为 `"semantic"`(与规则层 `source="rule"` 区分);`verdict` 是**语义建议**,非硬断言,`causal_claim=NONE` 保持,不改变硬可省数字。

#### Scenario: 回填合并显示

- **WHEN** agent 回填了某候选的 verdict
- **THEN** 报告显示该 verdict(标注 source=semantic),不改变硬可省量。

#### Scenario: 未回填时保守

- **WHEN** 候选未被 agent 回填
- **THEN** verdict 保持 `not_applicable`(未判定),不猜测。

### Requirement: 确定性可复现

AgentTrace 侧的候选清单与上下文构造 SHALL 是**纯函数、确定性**;agent 的 LLM 判定属外部语义判断,回填的 verdict 不参与 AgentTrace 的确定性计算(除展示外)。

#### Scenario: 候选清单确定性

- **WHEN** 同一 trace 两次生成候选清单
- **THEN** 输出逐字段一致。

### Requirement: 分析层门控与 additive

C1 候选清单语义层 SHALL 仅在 `enable_analysis=True`(或显式 `--semantic`)时生成;默认关闭 SHALL 不产生任何新输出,与 v0.6 逐字节一致。SHALL NOT 注册进 `ALL_DETECTORS` / `ALL_ATTRIBUTION_ENGINES`;SHALL NOT 改变现有 detector / CMP / THINK / RETRY / SUB / TOOL-004 的检测行为;`causal_claim=NONE`。

#### Scenario: 默认关闭零影响

- **WHEN** `enable_analysis=False` 运行完整 pipeline
- **THEN** 报告输出与 v0.6 逐字节一致。

#### Scenario: 不改变检测行为

- **WHEN** 开启语义层
- **THEN** `ALL_DETECTORS` / `ALL_ATTRIBUTION_ENGINES` 数量不变;现有 finding 不变。

### Requirement: 报告渲染与语义边界

`enable_analysis=True` 且候选清单/回填存在时,报告 SHALL 显示「语义判断(C1)」块:列出需语义判断的候选,及 agent 回填的 verdict(若有)。报告 SHALL NOT 出现 "wasted" / 因果断言;verdict 标注"语义建议,非硬断言";未回填的候选标注"待 agent 语义判断"。

#### Scenario: 渲染语义块

- **WHEN** 开启分析层且存在候选/回填
- **THEN** 报告含「语义判断(C1)」块,verdict 标"语义建议",无 wasted/因果断言。

#### Scenario: 未回填标注

- **WHEN** 候选未回填
- **THEN** 该候选显示"待 agent 语义判断",不虚构 verdict。
