# AgentTrace

> Harness 架构/工具性检测与 Token 归因引擎——以 Token 切入，诊断 Agent 执行缺陷，进而测出 Harness 架构(工具性)问题(Engine to diagnose agent-efficiency defects AND assess Harness architecture behavior via token invariants)

<p align="center">
  <img src="https://img.shields.io/badge/tests-248%20passed-brightgreen" alt="pytest 248 passed">
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="license MIT">
  <img src="https://img.shields.io/badge/python-3.13-blue" alt="python 3.13">
  <img src="https://img.shields.io/badge/DSH-0.1.1--rc.2-blue" alt="DeepSeek Harness 0.1.1-rc.2">
</p>

面向 DeepSeek Harness(DSH)会话日志。以测试因子分类法(Defect Taxonomy)统一组织 Agent 浪费诊断——检测执行缺陷、把判断归因到可验证证据、输出可行动建议。

**核心定位**：省 Token 只是副产品。真正的目标是用 **Token 作为切入点**，通过可验证的「Token 不变量」测出 **Harness 架构/工具性**问题（token 记账正确性、compaction 净账、fork/lineage 记账、缓存口径、schema 稳定性）。当前已实现的是 Agent 效率诊断与 Token 归因层；「Harness 架构不变量检查」是正在构建的架构评估层(路线图见 `agenttrace/ARCHITECTURE.md`)。

## 核心特性

- **免埋点**:离线读取 DSH 会话日志,无需侵入 agent
- **确定性规则**:输出 100% 可复现,不依赖 LLM 猜测(LLM 语义层已设计、未实现)
- **归因边界**:无证据不归因;`tokens=None` 表示 not applicable,不虚构 token 成本
- **六类 detector 谱系**:TOOL-001(cost 重复调用)/ CMP-001(observation 压缩)/ THINK-001(flag 推理强度)/ RETRY-001(reliability, usage=0 不虚构成本)/ SUB-001(observation 委托)/ TOOL-004(flag 无效参数重试,无 token 归因)
- **分析层**(默认关闭,开启不破坏确定性):反证(counter-evidence)+ 置信度完善 + 会话综合画像 + 上下文健康度观测(CTX-001)+ Token 记账双写不变量检查(A1)+ 跨会话 Lineage 血缘观测(A2)
- **五段式报告**:Signal / Evidence / Observed / Attribution / Interpretation

## 快速开始

```bash
# 分析一个 DSH 会话
python -m agenttrace.cli analyze <DSH会话目录>

# 列出全部 detector
python -m agenttrace.cli list-detectors

# 跑测试(248 个)
python -m pytest tests -q
```

## 示例输出

分析一个 DSH 会话、`--analysis` 开启分析层时的报告外观(示意,基于真实会话脱敏):

```text
# AgentTrace Diagnostic Report
会话: `<session-id>`  模型: `deepseek-v4-flash`
turns: 122  steps: 601  tool_calls: 591

## Summary
- TOOL-001: 10 个 finding
- 可归因成本(仅 cost): 9249 tokens
- Evidence 覆盖率: 100%(16/16)

### 综合判断
1. `TOOL-001#3` 重复工具调用(候选可避免成本) — 可归因成本 1544 tokens,置信度 0.50
**健康度概述:** detector 信号分布(cost 缺陷 8 处(候选可避免 ~7579 tokens)、观测 0 处、统计标记 7 处、可靠性 1 处;反证 15 条);建议优先核查 TOOL-001

### 上下文健康度(CTX-001)
- 当前上下文: 61200 tokens(input + cache_read)   [窗口未知,占用 not applicable]
- turn 数: 122
- 重复工具调用操作率: 12.3%(重复 73/591)

## Cost defects (候选可避免成本)
### TOOL-001 `duplicate_tool_call`
**Signal:** 重复工具调用:同一工具+等价参数被执行多次
**Evidence:** turn 26 step 1   **Observed:** occurrences=3
**Attribution:** 候选可避免成本 933 tokens(direct=590, propagated=343, unattributed=871)
**Interpretation:** 成本缺陷(候选可避免)——第 2..N 次调用可能冗余,建议核查循环/缓存逻辑
**Confidence:** 0.50(证据强度,非成本可信度)
**Counter-evidence:** 两次调用间隔大,中间可能有状态变化,重复可能是有意操作
```

完整样例见 `reports/e2e_analysis_on.md`(已脱敏)。

## 架构

```
Raw DSH → Adapter → Canonical Trace (turns/steps + events)
   → Detector Registry (6) → Finding[]
   → Attribution Registry (6) → Attribution[] (kind: cost/observation/flag/reliability)
   → Analysis (analysis/, 默认关闭) → Report (五段式 + 四组语义隔离)
```

## 文档

- `agenttrace/README.md` — 快速使用与里程碑
- `agenttrace/ARCHITECTURE.md` — 架构原则、归因边界、detector 谱系

## 许可证

[MIT](LICENSE)
