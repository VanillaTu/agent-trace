# AgentTrace — Harness 架构/工具性检测与 Token 归因引擎

> **定位**:以测试因子分类法(Defect Taxonomy)统一组织 Agent 浪费诊断;并以 **Token 为切入点**、通过可验证的「Token 不变量」测出 **Harness 架构/工具性**问题。**省 Token 是副产品**。
> **数据源**:DeepSeek Harness(DSH)会话日志;Adapter 层抽象了 harness 差异,可扩展其他 Agent harness。
> **状态**:v0.5 + 分析层 ✅(248 tests;`--analysis` 默认关闭保确定性);「Harness 架构/工具性检查」(Token 记账双写不变量、跨会话 Lineage)已落地(见 ARCHITECTURE).

## 一句话

```
agenttrace analyze <session>
```

→ 输出五段式诊断报告(Signal / Evidence / Observed / Attribution / Interpretation),让工程师拿到就能判断"这个值得调查"。

## 六类 detector 谱系

| Detector | Finding.kind | 语义 | tokens |
|---|---|---|---|
| TOOL-001 | cost | 重复工具调用(候选可避免成本) | 有 |
| CMP-001 | observation | 上下文压缩 shadowed | 观测 |
| THINK-001 | flag | 推理强度异常 | 观测 |
| RETRY-001 | reliability | 模型重试(可靠性) | None(usage=0) |
| SUB-001 | observation | subagent 委托拓扑 | None(无成本字段) |
| TOOL-004 | flag | 无效参数重试(可避免失败尝试标记) | None(无 usage) |

## 核心原则

> **Finding 说明"发现了什么性质的问题";Attribution 说明"这个判断落在 trace 的什么可验证证据上"。两者不强制绑定。**
> **Attribution = 映射到可验证证据/资源,而非强制转换成 token cost。**
> **没有证据就不做成本归因;causal_claim = NONE。**

## 快速开始

```bash
cd agenttrace   # 项目根目录

# 1. 跑测试(248 个全绿)
python -m pytest tests -v

# 2. 分析一个 DSH 会话(给目录)
python -m agenttrace.cli analyze "~/.dsh/sessions/<会话目录>"

# 2b. 或按会话 ID 解析(免手输目录,先用 list-sessions 拿到 ID)
python -m agenttrace.cli analyze --session-id <id> [--root <DSH sessions 根目录>]

# 3. 列出可分析的 DSH 会话
python -m agenttrace.cli list-sessions

# 4. 列出 detector
python -m agenttrace.cli list-detectors
```

## 架构

```
Raw DSH → Adapter → Canonical Trace (turns/steps + events)
   → Detector Registry (6 个) → Finding[]
   → Attribution Registry (6 个) → Attribution[] (kind: cost/observation/flag/reliability)
   → Report (五段式 + Summary + 四组语义隔离)
```

## 里程碑

```
✅ v0.1 Canonical Trace + DSH schema 反解
✅ TOOL-001 (cost defect)
✅ v0.2 Attribution Engine (cost 归因 + 证据链)
✅ CMP-001 (hard observation)
✅ THINK-001 (statistical flag, 分布驱动)
✅ RETRY-001 (reliability, usage=0)
✅ v0.3 checkpoint (kind 解耦 + 语义冻结)
✅ SUB-001 (topology observation)
✅ TOOL-004 (invalid-param retry, 可避免失败尝试标记)
✅ v0.4 correlation inventory (负结论 + 跨 session 方向)
✅ v0.5 First Useful Release (analyze + 五段式 + 陌生 trace E2E)
⏳ v0.6 Cross-Session Lineage (SUB → child session → TOOL/CMP/THINK/RETRY)
```

## 设计文档

- `agenttrace/ARCHITECTURE.md` — 架构原则 + 归因边界
- `05-项目设计-评审版.md` — 六份外部 AI 评审 + 事实核验
- `06-真实DSH日志Schema反解.md` — 真实 schema
- `07-源码与真实数据交叉验证.md` — 源码 × 真实数据
- `reports/` — 各 detector 的 evidence inventory + 报告样例
