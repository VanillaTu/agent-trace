# Proposal — b1-ab-validation (B1)

## Why

09 文档第 29 行:"修复前后 A/B 验证建议有效性(Token Reduction / Tool Call Reduction / Retry Rate / Task Success Rate)"——当前**未达成**。AgentTrace 实现了 TOOL-001(重复工具调用)和 TOOL-004(无效参数重试)两个 detector,并输出"候选可避免"建议,但从未**实测**这些建议是否真的能带来可观测的下降。B1 要补上这一环:把"有用"变成"实测"。

真实数据证据盘点(109 会话,子代理调研,evidence.md):
- **TOOL-001**:244 finding / 45 会话;可省 tool-call **394**(占全部 13,275 的 2.97%);按保守修复可删 290 个"仅重复"step,可归因 output token 约 **81,844**(粗口径 126,313)。
- **TOOL-004**:10 finding / 10 会话(每会话恰 1,几乎全是 `send_session_message`);可省 10 次工具调用 + 10 次工具级重试往返;失败生成 step 合计 usage **21,833**(input 11,002 / output 10,831)。
- 3 个代表会话 original vs fixed 保守对比:tool-call 下降 2–38 次/会话,token total 下降 4.7k–29k/会话。

**这暴露的关键方法论点**:AgentTrace 现有报告已经能"检出"缺陷并给建议,但**无法证明建议有效**。B1 通过真实会话的前后对比,把建议有效性变成可复现的观测证据——这是 AgentTrace 从"诊断工具"走向"有效诊断工具"的验证闭环。

## Goal

新增一个**修复前后 A/B 对比验证能力**(描述性前后对比,非新增 detector):对真实会话,分别以 **original**(全量)与 **fixed**(去掉 TOOL-001 重复调用 / TOOL-004 失败尝试)两种口径重述 trace,量化 tool-call 下降、删 step 数、output token 下降,并严格区分工具级重试与模型 API 重试。作为"建议有效性"的可复现实测。

**可交付形态(用户已定)**:**命令行/可复用报告** —— 新增分析层能力,一次跑出 original/fixed 对比报告(如 `b1-ab` 报告工具 / `analyze --ab` 模式),加 report 渲染 + 测试。让"修复前后对比"成为 AgentTrace 的可复用命令,而非一次性调研。
**验证集范围(用户已定)**:**固定验证集会话** —— 固定一组代表性真实会话(高 TOOL-001 + 含 TOOL-004,含 session_id),生成可复现 before_after 报告。强调"随数据给 session_id + 脚本",非通用节省率。

### 核心设计要点(基于 evidence 诚实边界)

1. **描述性前后对比,非因果实验**。`causal_claim = NONE`。这是"把同一真实会话的 trace 分别以 original/fixed 口径重述",不是实验组/对照组。只报"观测到的可省量",不判"修复后一定省 X"。
2. **指标分层,禁止混算**:
   - **硬指标(主)**:`tool-call 下降`(每 finding N−1,累计 394)与 `删除 step 数`(确定性、可复现、口径无关)。
   - **token 可信子指标**:`output token 下降`(保守 81,844 / 粗 126,313)。**不得把 input token 计入"省"**(它是上下文,占可省 ~95%)。
   - **retry 严格分开**:TOOL-001/004 只省**工具调用层重试**(TOOL-004 的 10 次),**不省 `llm/retry` 事件**(实测 0 变化,4→4→0)。严禁把"工具级重试"与"模型 API 重试(RETRY-001)"混为同一指标。
3. **报告口径必须标注**:每条数字写清是"整 step 可删"还是"含冗余 occurrence 即计";token 是"input+output"还是"仅 output";是"静态重述"还是"真实重跑"。给区间/两口径,不硬塞单一数字。
4. **语义判断显式隔离**:区分"高置信可省"(确定性重复:同参 `read`/`write`/`edit`、参数错误失败)与"轮询/重校验"(`list_*`/`get_state`/`session_status`,标记 `semantic=debated`,不计入硬可省)。B1 的"省"数字基于确定性重复子集,单独披露语义不明子集数量(否则被高估)。
5. **TOOL-004 样本不足**:10 条且集中在开发会话,统计力弱 → 定位为**机制/成因说明**(失败一次往返 + 失败生成 step 确有用量),不单独放大其量化效果;或扩大样本后再计量。

## Non-Goal

- **不改现有 detector 行为**:TOOL-001/TOOL-004/attribution 源码零改动,additive。
- **不做新增 detector**:B1 是验证/测量,不注册新的 `ALL_DETECTORS`。
- **不做真实模型重跑实验**(级联效应不可静态测量):B1 只做静态反事实重述,诚实标注"此为观测可省量,非真实重跑因果结果"。
- **不把 input token 当 defect cost**:不混算 Total wasted。
- **不判因果**:causal_claim=NONE。

## Testing

`tests/test_b1_ab_validation.py`:original/fixed 重述、tool-call 下降计算、delete step 数、output token 下降、retry 严格分开(工具级 vs `llm/retry` 不变)、语义隔离(轮询型 `semantic=debated`)、`causal_claim=NONE`、确定性、additive(现有报告逐字节不变)。+ 全量回归 + `check_facts`。
