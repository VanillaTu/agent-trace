# Change: detector-ctx-001

## Why

长会话上下文膨胀导致 Agent 效率退化(重复操作、工作记忆错误、用推测替代验证)是已观测到的真实风险,但现有 detector 只能检出"重复现象"(如 TOOL-001 能抓重复 zstd 解压),没有"上下文健康度"这个根因指标。CTX-001 作为分析层观测,提供可量化的退化风险信号,与 change#2 分析层画像(profile)同构、互补。

## What Changes

- 新增**分析层数据块** `ContextHealth`(与 change#2 的 `profile` 同构):从 trace 计算会话级观测指标——当前上下文 tokens(含 cache_read)、峰值 turn 数、重复工具调用操作率。
- 报告 `enable_analysis=True` 时渲染"上下文健康度"块(与综合判断块并列)。
- **量化"上下文压力"仅在窗口字段真实已知时给出**,否则显示 not applicable——不虚构窗口、不虚构压力结论(数据驱动铁律)。
- 定位分析层观测(统计标记;**不判因果**);不做成本归因。

## Capabilities

- **New Capabilities**:
  - `analysis/context-health` — 上下文健康度数据块(新增 `specs/analysis/context-health/spec.md`)
- **Modified Capabilities**: 无

## Impact

- 代码:新增 `agenttrace/analysis/context_health.py`(`ContextHealth` + `build_context_health`);`pipeline.py` 加 `DiagnosisResult.context_health` + Stage 3 生成;`report.py` 加健康度块。
- 测试:新增 `tests/test_context_health.py`;现有 114 测试全绿(**additive**)。
- **不做 Detector/Finding**;不注册 `ALL_DETECTORS`/`ALL_ATTRIBUTION_ENGINES`(会话级指标不检缺陷,归分析层)。
- 无破坏性变更。
