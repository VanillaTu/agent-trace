# Proposal — c1-semantic-judgment (C1)

## Why

项目三层评判(evaluate 设计为 **确定性规则 → 统计证据 → LLM 语义**),但 LLM 语义层从未实现——`detectors/base.py` 的 `CounterEvidence.source="semantic"` 标注"设计预留,本次不实现"。B1 暴露了最直接的缺口:B1 把轮询型工具(`list_agents`/`browser_get_state`/`list_sessions`)**一刀切全部标为 `semantic=debated`、不计入硬可省**——这是"双保险但没判断"的做法,无法区分"这次重复是真冗余"还是"合法轮询需刷新状态"。

**关键洞察(用户两次澄清)**:AgentTrace 是确定性 Python 工具,把 LLM 语义判断**内置进 AgentTrace 是错的方向**——它需要一个可用的 LLM endpoint(当前环境无:本地 ollama 超时、无 API key),且"规则假装 LLM"没有价值。**正确的做法是:LLM 语义层在"调用工具的 agent"身上**——DSH harness 的 agent 本身就是 LLM,让它用自身模型审视 AgentTrace 检出的候选重复。这天然解决 LLM endpoint 问题,并回归项目定位(测 harness 工具性:AgentTrace 作为工具被 agent 调用、agent 用 LLM 审视工具输出,正是"工具-智能体交互"验证)。

**这暴露一个架构级问题**:AgentTrace 检出的候选缺陷里,哪些是"确凿可省"(确定性重复)、哪些是"语义存疑"(需 LLM 判断)无法由规则层区分。C1 通过**候选清单 + 上下文输出**把语义判断交给 agent,补上三层评判的第三层。

## Goal

新增一个**语义判断候选清单层**:AgentTrace 把已检出的、需要语义判断的候选重复(尤其 `semantic=debated` 轮询型)以**适合 agent 一次性分析的结构化形式**暴露——每个候选附**需判断的上下文**(前后 step、工具结果、状态变化)。agent 调用工具读取后,用自身 LLM 判定每个"真冗余/合法 + 置信度 + 理由",回填进 AgentTrace 报告。

### 核心设计要点(按用户确认)

1. **不做 DSH 插件**:cordis/TS 跨语言成本高、偏离核心;保持独立工具,由 agent 调用(`python -m agenttrace.cli analyze <session>`,agent 即入口)。
2. **LLM 层在 agent 身上,不在 AgentTrace 进程**:AgentTrace 是确定性工具;语义判断由 harness 的 agent(本身是 LLM)完成。AgentTrace 不内置 LLM 调用。
3. **交付闭环**:AgentTrace 检出 candidate → 输出候选清单 JSON(每候选附上下文)→ agent 调用工具读取 → 自身 LLM 判定(真冗余/合法+置信度+理由)→ 回填(输出到文件/记录)→ AgentTrace 报告合并回填后的 verdict。
4. **确定性可复现**:AgentTrace 侧(候选清单、上下文构造)纯确定性;agent 的 LLM 判定属外部语义判断,回填进报告的 `verdict` 标注 `source="semantic"`(与规则层的 `source="rule"` 区分),`causal_claim=NONE` 保持:`verdict` 是"语义建议"不是"硬断言",不改变硬可省数字。
5. **入口**:CLI 加 `--semantic`(或 `analyze --semantic`),输出候选清单 JSON(供 agent 消费)。

## Non-Goal

- **不做 DSH 插件**,不做独立交互入口。
- **不内置 LLM 调用到 AgentTrace**(语义判断交给 agent)。
- **不改现有 detector 行为**:候选/上下文构造 additive,不改变 TOOL-001/TOOL-004 检出。
- **不改变硬可省数字**:agent 的 verdict 是语义标注,不进入硬可省量。
- **不虚构语义**:无 agent 回填时,verdict 保持未判定(not_applicable),不猜。

## Testing

`tests/test_c1_semantic.py`(或并入 b1):候选清单生成、上下文构造(前后 step/工具结果/状态变化)、语义优先输出轮询型、verdict 回填合并(标注 source=semantic)、causal_claim=NONE、additive(现有报告逐字节不变、detector 零改动)、确定性(候选清单/上下文纯函数)、无回填时 verdict 未判定。+ 全量回归 + `check_facts`。
