# THINK-001 Baseline:真实 reasoning 分布分析

> 阶段一:只建 baseline,不写 detector。数据来源:56 个真实 DSH 会话,2042 个含 reasoning_tokens 的 step。

---

## 一、全局 percentile

| percentile | reasoning_tokens | output_tokens | reasoning_ratio |
|---|---|---|---|
| P50 | 162 | 443 | 0.41 |
| P75 | 535 | 902 | 0.64 |
| P90 | 1162 | 1648 | 0.80 |
| P95 | 1498 | 2239 | 0.85 |
| P99 | **3451** | **4468** | **0.94** |

**关键观察**:
- reasoning_ratio 在 P90~P99 之间**非常平坦**(0.80 → 0.94),说明"相对强度"维度区分度差;
- reasoning_tokens 绝对量在 P90~P99 之间**陡增**(1162 → 3451),说明"绝对强度"是更好的异常信号;
- **reasoning_ratio 天然有上限(~1.0)**,因为绝大多数 step 的 output ≥ reasoning(当前 adapter 下)。ratio 不是好维度。

## 二、双维度分析(验证"绝对量 vs 相对量")

| 维度 | Top 案例 | 观察 |
|---|---|---|
| reasoning_tokens Top | 6453 / 5848 / 5440(全是 tool=True) | 绝对量高的几乎都是工具调用 step |
| reasoning_ratio Top | 1.00(no_tool,2048/2048)、0.98、0.97 | ratio 高只是"output 短",绝对量可能很小 |

**结论**:ratio 失真问题真实存在——`2048/2048=1.00` 看起来极端,但绝对量只有 2048,远低于 reasoning 6453 的案例。**第一版应以 reasoning_tokens 绝对量为主要信号**。

## 三、按 tool_call 分组

| 组 | n | reasoning P50 | P95 | P99 | ratio P50 | P95 |
|---|---|---|---|---|---|---|
| with_tool | 1787 | 165 | 1567 | 3505 | 0.43 | 0.86 |
| no_tool | 255 | 139 | 1258 | 2394 | 0.29 | 0.71 |

**观察**:工具调用 step 的 reasoning 中位数/分位数都更高——符合直觉(调用工具前需要更多推理)。

## 四、按 model 分组

| model | n | reasoning P50 | P95 | ratio P50 | P95 |
|---|---|---|---|---|---|
| deepseek-v4-flash | 1666 | 159 | 1669 | 0.40 | 0.86 |
| qwen3-vl:8b-instruct | 335 | 189 | 1183 | 0.42 | 0.82 |
| huihui_ai/qwen3.5-abliterated:9b | 41 | 146 | 385 | 0.43 | 0.73 |

**观察**:三个 model 分布接近,deepseek 的 P95 略高。样本内 model 差异不显著。

---

## 五、结论:THINK-001 规则建议(数据驱动)

### 1. 主信号 = reasoning_tokens 绝对量(不是 ratio)

原因:
- ratio 在 P90-P99 平坦(0.80→0.94),区分度差;
- ratio 有上限 ~1.0,且"低 output 高 ratio"的案例绝对量小、不构成真异常;
- 绝对量 P99=3451 是清晰的边界。

### 2. 候选阈值建议(待人工检查后确认)

```text
candidate anomaly:
    reasoning_tokens >= P95 (1498)  → severity=warning, confidence=medium
    reasoning_tokens >= P99 (3451)  → severity=info/high-flag, confidence=high
```

不机械用单一阈值,而是:
- **P95-P99 区间**:high-intensity flag(严重度 warning),不叫 defect;
- **> P99**:high-intensity flag(严重度 warning/info),**不声明 avoidable**。

### 3. 为什么不用 ratio 做阈值

数据证明 ratio 失真:`2048/2048=1.00` 但绝对量 2048,远低于绝对量 6453 的 step。若用 ratio P99(0.94)会漏掉真正高 reasoning 的 case,却标记低 output 的小 case。

### 4. 术语约束(保持克制)

- 叫 **reasoning-token intensity anomaly / high-intensity flag**
- **不叫 "过度推理"/"over-reasoning"**(trace 只能证明"消耗高",不能证明"不必要")
- **不声明 avoidable**(无 counterfactual evidence)

---

## 六、待人工检查

Top 1% (reasoning >= 3451) 共 21 个 step:
- 绝大多数是 `tool=True`(19/21);
- 集中在少数 session(如 <session-id-a>、<session-id-b>、<session-id-c>),且多个 session 出现相同 step 值 → **疑似同一模板/任务被不同会话复用**;
- 需要抽查 3-5 个实际 trace,确认它们是"复杂任务"还是"异常行为"。

**若人工检查发现这些确实是复杂任务 → P99 只是 high-intensity flag,不是 defect threshold。**

## 七、人工检查结果(<session-id-a> 真实 trace)

抽查含 reasoning=3505/4193 的 <session-id-a> 会话,发现:

### 1. 高 reasoning 的 step 是"决策点",不是普遍现象

- 一个 turn 内大量 `pwsh` 连续调用 step,**reasoning=0**(纯执行,不需要思考);
- 只有**少数关键 step** 出现高 reasoning:
  - `mnemon_document_search`(4193)、`mnemon_document_manage`(3690)——工具切换决策
  - 复杂 grep/read 后的总结 step(1004/1359/1602/3505)
  - 任务收尾的总结 step(947/819/808)

### 2. 结论:高 reasoning ≈ 决策点/复杂任务,非"过度推理"

**数据支持"P99 只是 high-intensity flag,不是 defect threshold"**:
- 高 reasoning 出现在"需要判断、切换工具、总结"的时刻,这是**正常且必要**的;
- 连续执行型 step 反而 reasoning=0;
- 没有观察到"某一步明显不需要思考却花了大量 reasoning"的病理案例。

### 3. 对 THINK-001 规则的最终建议

```
第一版:THINK-001 = reasoning-token intensity flag(非 defect)

检测:
    reasoning_tokens >= P95 (1498)  → high-intensity flag, severity=info
    reasoning_tokens >= P99 (3451)  → extreme flag, severity=info

不声明:
    - 不叫 "over-reasoning" / "过度推理"
    - 不声明 avoidable
    - 不自动判定缺陷

证据(输出到 report):
    reasoning_tokens / output_tokens / reasoning_ratio
    baseline: P50/P95/P99
    flag 依据:reasoning_tokens >= P99
    tool_call 是否伴随(帮助人工判断)

置信度:
    reasoning >= P99 且有 tool call  → 0.9(决策点特征明确,但非缺陷)
    reasoning >= P99 无 tool call    → 0.8(更可疑,但仍不叫缺陷)
    P95 <= reasoning < P99          → 0.7
```

### 4. 为什么第一版只做 flag 不做 defect

从 trace 能证明的:reasoning consumption unusually high。
不能证明的:reasoning was unnecessary(需要 counterfactual,当前无证据)。

**数据明确支持**:高 reasoning 大多伴随工具调用决策,是正常复杂任务的特征。因此 THINK-001 第一版定位为 **observability flag**,为后续"reasoning intensity relative to execution context"研究留基础,不提前设计。
