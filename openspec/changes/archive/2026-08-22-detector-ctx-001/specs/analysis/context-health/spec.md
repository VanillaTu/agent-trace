# Spec — analysis/context-health

## Purpose

为 AgentTrace 增加一个**分析层数据块** `ContextHealth`,对会话做"上下文健康度"观测:当前上下文 tokens(含 cache_read)、峰值、turn 数、重复工具调用操作率。它挂在 `DiagnosisResult.context_health`,与 change#2 的 `profile` 同构,仅在 `enable_analysis=True` 时生成并渲染。**量化"上下文压力"只在窗口字段真实已知时给出**,否则不虚构(数据驱动与无证据不判铁律)。

## ADDED Requirements

### Requirement: ContextHealth 数据块计算

当一个 trace 含至少一个 step 时,分析层 SHALL 生成一个 `ContextHealth` 数据块,含确定的会话级观测指标。指标纯由 trace 数据计算,`tokens=None`(不适用)语义区别于 `0`。
- 当前上下文 tokens SHALL = 末 step 的 `input_tokens + cache_read_tokens`(含缓存重读,排除 cache_write)。
- 峰值上下文 tokens SHALL = 所有 step 中 `input_tokens + cache_read_tokens` 的最大值。
- turn 数 SHALL 等于 trace 的 turn 总数。
- 重复工具调用操作率 SHALL = `重复调用数 / 工具调用总数`;无工具调用时 SHALL 为 `None`(not applicable),非 `0`。
- 空会话(无 step)SHALL 返回一个全 not-applicable 的块(指标置空/`None`),不得虚构数值。

#### Scenario: 归一化上下文口径
- **WHEN** 一个 step 的 `input_tokens=1000`、`cache_read_tokens=5000`
- **THEN** 当前上下文 tokens = 6000(1000 + 5000),不排除 cache_read,M1 口径。

#### Scenario: 无工具调用时重复率不适用
- **WHEN** trace 有 step 但无任何 tool_calls
- **THEN** 重复工具调用操作率 = `None`(报告显示"无工具调用"),而非 `0.0`。

#### Scenario: 空会话
- **WHEN** trace 无任何 step
- **THEN** ContextHealth 所有指标置 not-applicable(当前/峰值 tokens = 0、turn = 0、repeat_rate = None、window = unknown、occupancy = None)。

### Requirement: 窗口解析不虚构(数据驱动)

`ContextHealth` 的上下文窗口 SHALL 只来自 trace 的 `metadata["context_window"]`(真实已知)。当该字段缺失时,SHALL:窗口 tokens = `None`、窗口来源 = `"unknown"`、占用率 = `None`、压力标记 = `False`(**不虚构窗口、不产虚假压力结论**)。

#### Scenario: 窗口字段缺失
- **WHEN** trace.metadata 不含 `context_window`(当前 adapter 正常产出即如此)
- **THEN** 占用率 = `None`(报告显示 not applicable),压力标记恒 `False`,只输出真实观测指标。

#### Scenario: 窗口字段存在
- **WHEN** trace.metadata 含 `context_window`(如 128000)
- **THEN** 占用率 = `当前上下文 tokens / 128000`,窗口来源 = `"metadata"`,可按阈值判压力。

### Requirement: 压力标记仅在真实窗口下判定

`ContextHealth.pressure_high` SHALL 仅在窗口来源为 `"metadata"`(真实窗口)且占用率超过阈值时置 `True`;窗口未知时 SHALL 恒 `False`。阈值 `OCCUPANCY_HIGH_WATERMARK` 是**占位待校准常量**(不虚构数据支撑),仅在窗口真实已知时参与判定。

#### Scenario: 窗口未知不判压力
- **WHEN** 窗口来源 = `"unknown"`(无 context_window 字段)
- **THEN** `pressure_high` = `False`,即使 `current_context_tokens` 很大。

#### Scenario: 真实窗口超阈值
- **WHEN** 窗口来源 = `"metadata"` 且 `occupancy_ratio > 0.70`
- **THEN** `pressure_high` = `True`(报告提示"上下文压力高,建议压缩")。

### Requirement: 重复组排序确定性

`ContextHealth` 的记录"重复最严重 fingerprint 组"时 SHALL 确定性排序(重复次数降序 → tool_name 升序 → fingerprint 升序),保证两次运行结果逐字段一致。

#### Scenario: 重复组 tie-break
- **WHEN** 存在两个重复组且重复次数相等
- **THEN** 排序按 tool_name 升序,再按 fingerprint 升序,结果确定。

### Requirement: 分析层门控与 additive

`ContextHealth` SHALL 仅在 `enable_analysis=True`时生成与渲染,默认(关闭)SHALL 不产生任何新输出,与 v0.5 逐字节一致。SHALL NOT 注册进 `ALL_DETECTORS`/`ALL_ATTRIBUTION_ENGINES`;SHALL NOT 改变现有 detector 的检测行为。

#### Scenario: 默认关闭零影响
- **WHEN** 未开分析层(`enable_analysis=False`)运行完整 pipeline
- **THEN** `DiagnosisResult.context_health` = `None`,报告输出与 v0.5 逐字节一致。

#### Scenario: 不进入检测/归因体系
- **WHEN** 开启分析层
- **THEN** `ALL_DETECTORS`/`ALL_ATTRIBUTION_ENGINES` 仍为 5 个;attributions 不含 CTX-001(无成本归因)。

### Requirement: 报告渲染与语义边界

`enable_analysis=True` 时,报告 SHALL 渲染一个"上下文健康度"块(与综合判断块并列),含当前/峰值上下文 tokens、turn 数、重复操作率、窗口(未知时显示 not applicable)、压力标记(仅 pressure_high 时提示)。报告 SHALL NOT 将该块伪装成成本归因,SHALL NOT 输出因果断言("上下文大导致退化"),只能陈述"占用高关联退化风险(相关性非因果)"。

#### Scenario: 渲染健康度块
- **WHEN** 开启分析层且 trace 有 step
- **THEN** 报告含"上下文健康度"块,当前上下文/峰值/turn 数/重复率显示真实值;窗口 unknown 时占用显示 not applicable。

#### Scenario: 语义边界
- **WHEN** 报告渲染健康度块
- **THEN** 该块不出现任何 token 成本数字或因果断言(如"浪费/导致"),只陈述关联退化风险。
