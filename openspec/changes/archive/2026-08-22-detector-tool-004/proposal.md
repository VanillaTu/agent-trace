# Change: detector-tool-004

## Why

工具调用缺参报错后"同类重试成功"(无效参数重试)是 Agent 执行的可避免缺陷,但现有 5 个 detector 全部漏检:TOOL-001 因两次调用参数不完全一致(缺参)走中间档不判重复,RETRY-001 只认模型层 `llm/retry`,不管工具调用失败后的重试。这正是最初设计(05 文档 L558 `invalid param retry`)定义过、但一直未实现的 TOOL 因子。盲区审计(BL-001)已拿到完整真实证据链,现在补上时机成熟。

## What Changes

- 新增 detector `TOOL-004 invalid-param-retry`:检测 `tool/result` 为参数错误(`invalid arguments` / `missing required` / `invalid_request`),标记该次失败调用;同一 callId 或相邻 step 有重试成功 → 归因"可避免的失败尝试"。
- 定位 `observation`/`flag` + 反证;**不估算成本**——失败 attempt 无 usage(tokens=None,归因边界铁律,不虚构 token 成本)。
- 注册进 Detector Registry + Attribution Registry + 报告集成。

## Capabilities

- **New Capabilities**:
  - `detectors/tool-004` — 无效参数重试检测(新增 `specs/detectors/tool-004/spec.md`)
- **Modified Capabilities**: 无

## Impact

- 代码:新增 `detectors/tool_004.py`、`attribution/tool_004.py`;`detectors/__init__.py` 与 `attribution/__init__.py` 注册;`report.py` 整合。
- 测试:新增 `tests/test_tool_004.py`;现有 113 个测试行为不变 + 1 个 registry 快照测试更新(`test_registry_has_five_detectors` 5→6)+ 文档事实同步(跑 `scripts/check_facts.py`)。**additive 铁律指不改现有 detector 行为**;注册表与文档计数属命中面同步更新,非行为变更。
- 无破坏性变更(不修改现有 detector 行为)。
