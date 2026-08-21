## Why

v0.5 已实现检测 + 归因闭环(5 类 detector,83/83 测试),但对照最初目标,"分析层"尚未完成:三层评判只实现了规则层与统计层,**LLM 语义层未实现**;设计中有 **counter-evidence** 但未落地;报告只有五段式 finding,**缺会话级综合画像**(把 5 类信号合成"这个会话的健康度 / 最值得调查的 2-3 条")。补上分析层,引擎才从"检测工具"成为"分析引擎",这也是"分析"维度的核心证据。

## What Changes

- **反证(counter-evidence)**:每个 Finding 增加反证列表——列出"可能推翻此 finding 的证据方向";规则层生成静态反证(如"该重复调用间隔 > N 步,可能是状态依赖";"无状态工具,可能是有意复查")。
- **置信度完善**:每个 Finding 基于证据强度完善置信度——有状态工具重复且间隔小 → 高置信;间隔大或无状态工具 → 降置信并附反证。置信度沿用现有 `Finding.confidence` 字段(非新增),画像排序使用精化后的值。
- **会话级综合画像**:报告 Summary 区新增"综合判断"块——汇总 5 类信号,给出:该会话最值得调查的 2-3 条(按可归因成本 × 置信度排序)、信号分布一句话概述。
- **LLM 语义层(仅设计预留,本次不实现)**:三层评判第三层——仅在"规则 + 统计均无结论"且用户显式开启时兜底;本次只写设计说明,不产生代码、不进 tasks、不纳入 archive。
- **BREAKING**:无(所有新增均为 additive;现有 finding schema 向后兼容,新增可选字段)。

## Capabilities

### New Capabilities

- `analysis/counter-evidence`: Finding 反证 + 置信度完善的行为规格(纯规则静态反证,置信度沿用现有字段)。
- `analysis/session-profile`: 会话级综合画像的行为规格(汇总规则、排序键、输出位置)。

> LLM 语义层(三层评判第三层)仅作设计预留:完整设计见 design.md D6,不产生 spec/代码,不纳入 archive。

### Modified Capabilities

- 无(现有 detection/attribution/report 行为不改变,仅 report 的 Summary 区扩展)。

## Impact

- **代码**:`agenttrace/detectors/base.py`(Finding 加 counter_evidence 字段)、`agenttrace/analysis/`(新模块:counter_evidence / profile,纯规则,无 LLM)、`agenttrace/report.py`(per-finding Confidence/Counter-evidence 渲染 + Summary 综合判断块)、`agenttrace/pipeline.py`(分析阶段挂载在 attribution 之后,默认 off)。
- **测试**:新增 N 个测试(默认关闭不影响确定性、反证规则含无状态分支、置信度完善、画像排序、开关门控、逐字节对比);现有 83 测试必须全绿(确定性铁律)。
- **依赖**:无新增(分析层纯规则,不引入 LLM 依赖)。
- **文档**:ARCHITECTURE.md 更新三层评判完整描述(LLM 层标注设计预留);09-最初目标对照标注.md 更新"分析层"状态。
