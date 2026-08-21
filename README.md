# AgentTrace

> Agent 执行轨迹效率缺陷检测与 Token 归因引擎(Agent execution-efficiency defect detection & token attribution engine)

面向 DeepSeek Harness(DSH)会话日志,以测试因子分类法(Defect Taxonomy)统一组织 Agent 浪费诊断——检测执行缺陷、把判断归因到可验证证据、输出可行动建议,让工程师拿到报告就能判断"这个值得调查"。

## 核心特性

- **免埋点**:离线读取 DSH 会话日志,无需侵入 agent
- **确定性规则**:输出 100% 可复现,不依赖 LLM 猜测(LLM 语义层已设计、未实现)
- **归因边界**:无证据不归因;`tokens=None` 表示 not applicable,不虚构 token 成本
- **五类 detector 谱系**:TOOL-001(cost 重复调用)/ CMP-001(observation 压缩)/ THINK-001(flag 推理强度)/ RETRY-001(reliability, usage=0 不虚构成本)/ SUB-001(observation 委托)
- **分析层**(默认关闭,开启不破坏确定性):反证(counter-evidence)+ 置信度完善 + 会话综合画像
- **五段式报告**:Signal / Evidence / Observed / Attribution / Interpretation

## 快速开始

```bash
# 分析一个 DSH 会话
python -m agenttrace.cli analyze <DSH会话目录>

# 列出全部 detector
python -m agenttrace.cli list-detectors

# 跑测试(114 个)
python -m pytest tests -q
```

## 架构

```
Raw DSH → Adapter → Canonical Trace (turns/steps + events)
   → Detector Registry (5) → Finding[]
   → Attribution Registry (5) → Attribution[] (kind: cost/observation/flag/reliability)
   → Analysis (analysis/, 默认关闭) → Report (五段式 + 四组语义隔离)
```

## 文档

- `agenttrace/README.md` — 快速使用与里程碑
- `agenttrace/ARCHITECTURE.md` — 架构原则、归因边界、detector 谱系

## 许可证

[MIT](LICENSE)
