# analysis/counter-evidence Specification

## Purpose
为每条检测发现(finding)补充两类分析元数据——反证(counter-evidence)与置信度(confidence):反证显式列出"可能推翻此发现"的证据方向,置信度由可验证证据强度推导;两者只做分析、不参与成本归因,让报告区分"高置信候选"与"存疑发现"。全部由确定性规则生成,默认关闭,不影响现有默认输出。

## Requirements

### Requirement: Finding 携带反证列表(counter-evidence)

每条检测发现 SHALL 携带一个反证列表;每个反证条目 SHALL 描述一个"可能推翻此发现"的证据方向(如"两次调用间隔大,中间可能有状态变化,重复可能是有意操作")。

- 反证条目 SHALL 至少包含:证据方向描述 + 来源(规则层静态生成)。
- 反证 SHALL 由确定性规则生成,不得依赖随机数、时间或外部服务。
- 反证是分析元数据:SHALL NOT 改变该 finding 的成本归因数字,SHALL NOT 为反证发明 token 成本。
- 未开启分析层时,finding 的反证列表 SHALL 为空,报告输出与现状完全一致。

#### Scenario: 规则层生成静态反证

- **WHEN** 分析层开启,且 TOOL-001 发现两次相同工具调用间隔超过阈值 N 步
- **THEN** 该 finding 的反证列表包含一条"间隔大,中间可能有状态变化 → 可能是有意操作"方向的反证,来源为 rule

#### Scenario: 反证不改变成本归因

- **WHEN** 某 finding 存在反证条目
- **THEN** 其 attribution 的 token 数字保持原值,报告中不出现由反证推导出的任何 token 成本

#### Scenario: 默认路径反证为空

- **WHEN** 未开启分析层运行完整 pipeline
- **THEN** 所有 finding 的反证列表为空,报告输出与 v0.5 完全一致

#### Scenario: 反证生成确定性

- **WHEN** 同一 trace 两次运行分析层
- **THEN** 两次生成的反证列表逐条相同

### Requirement: 置信度由证据强度推导

每条检测发现 SHALL 具有 0.0–1.0 的置信度;分析层开启时,置信度 SHALL 基于可验证证据强度计算(如重复调用间隔、参数一致性、工具状态性),存在强反证时 SHALL 降低置信度并附带对应反证条目。

- 置信度语义 SHALL 明确:高置信 = 证据强且无强反证;低置信 = 存在反证或证据弱。
- 置信度是分析元数据:SHALL NOT 参与成本归因计算,SHALL NOT 修正 attribution 的 token 数字。
- 未开启分析层时,finding 的置信度 SHALL 保持 detector 现有输出值,不做任何改写。

#### Scenario: 证据强 → 高置信

- **WHEN** 分析层开启,且 TOOL-001 的两次调用间隔小(≤ 阈值 N 步)且参数完全相同
- **THEN** 该 finding 置信度为高置信档(≥ 0.9),且无强反证条目

#### Scenario: 证据弱/有反证 → 低置信并触发反证

- **WHEN** 分析层开启,且两次调用间隔大(> 阈值 N 步)
- **THEN** 置信度降至低置信档(< 0.6),且反证列表包含"间隔大 → 可能有意操作"条目

#### Scenario: 无状态工具 → 保持低置信并附反证

- **WHEN** 分析层开启,且 TOOL-001 检测到 `STATELESS_TOOLS` 集合内的工具(如 web_search/get_time)重复调用
- **THEN** 置信度保持 detector 低置信(0.55),反证列表包含"无状态工具,结果可能随时间变化,重复不必然浪费"条目

#### Scenario: 中间档(间隔 ≤ N 但参数不完全一致)

- **WHEN** 分析层开启,且两次调用间隔 ≤ 阈值 N 步但参数不完全一致
- **THEN** 置信度保持 detector 原值,反证列表为空(不额外降置信、不额外加反证)

#### Scenario: 置信度值域与确定性

- **WHEN** 对任意 finding 计算置信度
- **THEN** 置信度始终在 0.0–1.0 之间,且同一 trace 两次运行结果相同

#### Scenario: 默认不改写置信度

- **WHEN** 未开启分析层运行完整 pipeline
- **THEN** 每个 finding 的置信度与 detector 现有输出值一致

### Requirement: 报告呈现反证与置信度(仅分析层开启时)

分析层开启时,报告的每条 finding 段落 SHALL 呈现其置信度与反证条目;无反证时 SHALL 标注"无反证"。未开启分析层时,报告 SHALL NOT 出现任何反证或置信度内容。

- 反证的呈现 SHALL 明确标注为"反证(可能推翻此发现的方向)",SHALL NOT 与证据(evidence)混排。
- 置信度的呈现 SHALL 标注其语义(证据强度),SHALL NOT 表述为成本金额的可信度。

#### Scenario: 分析层开启时呈现

- **WHEN** 分析层开启,且某 finding 存在反证
- **THEN** 报告该 finding 段落包含置信度与反证条目列表,且反证条目标注"反证"

#### Scenario: 分析层关闭时隐藏

- **WHEN** 未开启分析层
- **THEN** 报告不包含任何反证或置信度内容,输出与 v0.5 逐字节一致
