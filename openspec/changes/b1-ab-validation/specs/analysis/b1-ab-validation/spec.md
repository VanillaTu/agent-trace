# Spec — analysis/b1-ab-validation (B1)

## Purpose

为 AgentTrace 增加一个**修复前后 A/B 对比验证**能力(分析层数据块 `ABResult`):对单个真实会话,分别以 **original**(全量)与 **fixed**(去掉 TOOL-001 重复调用 / TOOL-004 失败尝试)两种口径**静态重述** trace,量化 tool-call 下降、删 step 数、output token 下降,并严格区分工具级重试与模型 API 重试。它挂在 `DiagnosisResult.ab_result`(仅 `enable_analysis=True` 时生成并渲染)。**只报观测可省量,causal_claim=NONE,不混算 Total wasted,不把 input token 当省(仅 output 是可信子指标),retry 严格分开(工具级 vs llm/retry),语义判断显式隔离(轮询型标 semantic=debated)。**

## ADDED Requirements

### Requirement: original 基线计算

分析层 SHALL 从 `trace` 计算 original 基线:`original_steps` / `original_tool_calls` / `original_output_tokens` / `original_input_tokens` / `original_total_tokens`。全部为确定性求值,无外部调用。

#### Scenario: 空 trace

- **WHEN** trace 无 step
- **THEN** 各 original 字段 = 0,不抛异常。

### Requirement: fixed 重述与可省量计算(保守模型)

分析层 SHALL 构建 fixed 口径(保守模型:整 step 上所有 tool_call 均为冗余才删该 step):

- `tool_call_reduction`:确定性重复子集(N>1 的 fingerprint 组)中去掉后续重复的 tool-call 数(每 finding 省 N−1)。
- `deleted_steps`:保守模型下整 step 可删的 step 数(TOOL-001 + TOOL-004 并集去重)。
- `output_token_reduction`:被删 step 的 output_tokens 合计(token 维度最可信子指标)。
- `input_token_change` / `total_token_change`:被删 step 的 input / input+output 合计,标注"上下文变化,非可省成本"。

#### Scenario: 单组重复 N=2

- **WHEN** 一个 fingerprint 组出现 2 次(确定性重复)
- **THEN** `tool_call_reduction` = 1。

#### Scenario: 共享 step 不整删

- **WHEN** 一个 step 含 2 个 tool-call,其中仅 1 个为冗余
- **THEN** 该 step 不删(保守模型),`deleted_steps` 不计入。

### Requirement: retry 严格分开

分析层 SHALL 区分两类重试,严禁混算:
- `tool_level_retries_saved`:工具调用层重试可省数(TOOL-004 失败 attempt 数)。
- `llm_retry_original` / `llm_retry_fixed` / `llm_retry_change`:模型 API 重试(llm/retry 事件)在 original 与 fixed 的计数;固定为不变(`llm_retry_change=0`,静态重述口径),并加防御性扫描:若 llm/retry 事件关联到被删 step,记录 warning。

#### Scenario: 工具级 vs 模型 API 重试

- **WHEN** trace 含 TOOL-004 失败 attempt 与 llm/retry 事件
- **THEN** `tool_level_retries_saved > 0` 但 `llm_retry_change = 0`。

### Requirement: 语义判断显式隔离

分析层 SHALL 用 `SEMANTIC_DEBATED_TOOLS`(轮询型工具集合)区分"确定性重复"与"语义存疑重复":轮询型工具(list_agents / list_sessions / session_status / browser_get_state / job_list / job_output / read_session / memory_list / browser_navigate)的冗余 occurrence 计入 `semantic_debated_occurrences` / `semantic_debated_steps`,**不计入硬可省**。

#### Scenario: 轮询型不计入硬可省

- **WHEN** 只含轮询型工具重复
- **THEN** `tool_call_reduction = 0`,但 `semantic_debated_occurrences > 0`。

### Requirement: causal_claim 与口径标注

`ABResult.causal_claim` SHALL 恒为 `"NONE"`;`method` SHALL 恒为 `"static_restatement"`。报告 SHALL 标注口径(保守模型 / 静态重述),并注明只报观测可省量、非因果断言。

#### Scenario: 恒 NONE

- **WHEN** 任意输入
- **THEN** `ABResult.causal_claim == "NONE"`, `method == "static_restatement"`。

### Requirement: 分析层门控与 additive

`ABResult` SHALL 仅在 `enable_analysis=True` 时生成;默认关闭 SHALL 不产生任何新输出,与 v0.5 逐字节一致。SHALL NOT 注册进 `ALL_DETECTORS` / `ALL_ATTRIBUTION_ENGINES`;SHALL NOT 进入 findings / attributions;SHALL NOT 改变现有 detector 的检测行为;SHALL NOT 修改 tool_004.py 源码(参数错误判定逻辑在分析层内联,与源头保持同步)。

#### Scenario: 默认关闭零影响

- **WHEN** `enable_analysis=False` 运行完整 pipeline
- **THEN** `DiagnosisResult.ab_result` = `None`,报告输出与 v0.5 逐字节一致。

#### Scenario: 不改 detector 源码

- **WHEN** 运行分析层
- **THEN** `ALL_DETECTORS` / `ALL_ATTRIBUTION_ENGINES` 数量不变;tool_004.py 未被修改。

### Requirement: 报告渲染与语义边界

`enable_analysis=True` 且 `ab_result is not None` 时,报告 SHALL 渲染「A/B 验证 — 修复前后对比 (B1)」块,含 original vs fixed 对比表、retry 分开表、semantic=debated 披露、TOOL-004 机制说明、口径标注。报告 SHALL NOT 出现 "wasted" / "Total wasted" / 因果断言;input token 标注"上下文变化,非可省成本";只报观测可省量。

#### Scenario: 渲染 AB 块

- **WHEN** 开启分析层且 ab_result 非 None
- **THEN** 报告含「A/B 验证」块;input token 标"上下文变化,非可省成本";无 "wasted" / 因果断言。

### Requirement: 固定验证集与可复现 fixture

分析层 SHALL 提供 `AB_VALIDATION_SESSIONS`(固定验证集会话,含 session_id + expected 下界)。验证集会话 SHALL 导出为 `tests/fixtures/b1_validation_sessions/{session_id}.json`(CanonicalTrace.to_dict()),供跨机可复现测试用;本机 E2E(标记 dsh_data)仅作额外验证,可复现性以 fixture 为准。

#### Scenario: fixture 存在且可解析

- **WHEN** 运行验证集测试
- **THEN** fixture 文件存在且可解析为 Canonical Trace。
