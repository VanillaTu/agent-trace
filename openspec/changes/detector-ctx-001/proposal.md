# Change: detector-ctx-001

## Why

长会话上下文膨胀导致 Agent 效率退化(重复操作、工作记忆错误、用推测替代验证)是已观测到的真实风险,但现有 detector 只能检出"重复现象"(如 TOOL-001 能抓重复 zstd 解压),没有"上下文健康度"这个根因指标。CTX-001 作为统计标记提供可量化的退化风险信号,与 change#2 分析层画像互补——这是最初设计(05 文档)定义过、但未实现的历史膨胀类别(CTX-001)。

## What Changes

- 新增 `CTX-001 context-health`:报告新增"上下文健康度"块——当前上下文 tokens/窗口占比、turn 数、重复操作率;占用超阈值(如 >70%)标记"上下文压力高,建议压缩"。
- 定位 `flag` 统计标记;**不判因果**(上下文大 ≠ 必然退化,但关联退化风险);输出观察值、不做成本归因。
- 报告集成(与 change#2 的综合判断块并列)。

## Capabilities

- **New Capabilities**:
  - `detectors/ctx-001` — 上下文健康度统计标记(新增 `specs/detectors/ctx-001/spec.md`)
- **Modified Capabilities**: 无

## Impact

- 代码:新增 `detectors/ctx_001.py`;`detectors/__init__.py` 注册;`report.py` 新增健康度块。
- 测试:新增 `tests/test_ctx_001.py`;现有 114 测试全绿(**additive**)。
- 无破坏性变更。
