# v0.4 Correlation Inventory(第一阶段)

> 只做共现分析,不写 correlation detector。数据:57 个真实会话,307 个 finding。

---

## 一、基础数据

| rule | finding 数 | turn 级出现率 P(rule) |
|---|---|---|
| TOOL-001 | 114 | 0.439 |
| THINK-001 | 103 | 0.463 |
| CMP-001 | 64 | 0.110 |
| SUB-001 | 15 | 0.183 |
| RETRY-001 | 11 | 0.085 |

total turns (with findings): 82

## 二、session 级共现

| pair | 共现 session 数 |
|---|---|
| SUB × THINK | 8 |
| THINK × TOOL | 6 |
| SUB × TOOL | 5 |
| CMP × RETRY / CMP × THINK / CMP × TOOL / RETRY × THINK / RETRY × TOOL | 各 1 |

## 三、turn 级共现 + 条件概率 + lift(核心)

| pair | P(B\|A) | P(B\|¬A) | lift | n_AB | n_A |
|---|---|---|---|---|---|
| RETRY → THINK | 0.286 | 0.480 | **0.60** | 2 | 7 |
| SUB → CMP | **0.000** | 0.134 | 0.00 | 0 | 15 |
| TOOL → THINK | 0.444 | 0.478 | 0.93 | 16 | 36 |
| TOOL → RETRY | 0.083 | 0.087 | 0.96 | 3 | 36 |
| SUB → THINK | **0.000** | 0.567 | 0.00 | 0 | 15 |
| SUB → TOOL | **0.000** | 0.537 | 0.00 | 0 | 15 |

## 四、⭐ 关键结论(数据驱动的诚实发现)

### 1. 没有强关联(lift 全部 ≤ 1.0)

所有 pairwise 的 lift 都在 0.6~0.96 之间,**没有任何 pair 呈现强正相关**。这意味着:

> 五类 detector 在真实数据里是**相互独立的事件**,不是"一个触发另一个"的关系。

**这本身是重要结论**:不能把任何 pair 升级成 correlation detector。

### 2. SUB-001 与所有其他 rule 在同 turn 零共现(n_AB=0)

这是最有力的发现。SUB-001 与 CMP/THINK/TOOL/RETRY 在同 turn 都**完全零共现**。

**解释**(数据机制):subagent 通过 `fork`/`spawn` 运行在**独立 session**里,它的工具调用/compaction/reasoning 都记在自己的 session,不进入 parent 的 turn。所以 parent 的 turn 里只有 `subagent/descriptor` 一条记录,不含 subagent 内部的 TOOL/CMP/THINK。

**含义**:SUB 的 topology 是"隔离的",跨 session 的 correlation 无法用 turn 级共现捕捉。这解释了为什么 SUB×CMP 无共现——不是没有关系,而是**关系跨越了 session 边界,当前 turn 级分析看不到**。

### 3. RETRY × THINK:lift=0.60(负相关倾向)

RETRY turn 里 THINK 出现率(0.286)**低于** baseline(0.480)。

**含义**:出现 retry 的 turn 反而**更少**伴随高 reasoning。这反直觉——原本猜测"复杂任务(高 reasoning)更容易触发 retry",但数据显示相反(retry 主要是 TRANSPORT/RATE_LIMIT 等基础设施错误,与 reasoning 强度无关)。

### 4. TOOL × THINK / TOOL × RETRY:lift ≈ 1(无关联)

接近 1,说明工具调用与 reasoning/retry 在 turn 级**彼此独立**,不存在"工具调用多 → 推理多"或"工具调用多 → 重试多"的模式。

## 五、对 Correlation Layer 的设计启示

1. **不做 pairwise correlation detector**——数据证明五类信号独立,强行升级成 CORR-001 会过度解释;
2. **真正的相关性可能跨 session 边界**(SUB 的 fork/spawn 子会话),需要**跨 session correlation**,而不是 turn 级;
3. **CORR 若要做,第一步应是"跨 session 的 SUB→子会话行为关联"**,而非同 turn 的 pairwise;
4. **causal_claim = NONE 完全成立**——不仅是不该claim,而且是数据根本不支持任何 causal 关联。

## 六、Correlation 概念(v0.4 引入,但暂不实例化)

```python
Correlation:
    correlation_id
    signal_a / signal_b
    scope: session | turn | step_window | cross_session
    temporal_relation: a_before_b | b_before_a | same_window | n_a
    strength: observational
    causal_claim: NONE  # 铁律
```

**第一版结论:Correlation Layer 的语义边界已建立,但真实数据不支持任何 pairwise 升级。真正的 correlation 价值在跨 session(SUB→子会话),留作后续。**

## 七、v0.4 第一阶段成功标准对照

| # | 标准 | 结果 |
|---|---|---|
| 1 | 五类 detector 进入 correlation input | ✅ |
| 2 | session/turn/step 三级 scope | ✅(step 级未详析,但 turn 级已充分) |
| 3 | temporal ordering 可重建 | ✅(turn 级可重建) |
| 4 | pairwise co-occurrence | ✅ |
| 5 | conditional probability | ✅ |
| 6 | baseline comparison | ✅(P(B\|¬A)) |
| 7 | step distance | ⚠️ 未单独算,但 turn 级已揭示无关联 |
| 8 | correlation evidence chain | ✅(数据即证据) |
| 9 | 禁止 causal claim | ✅(全部 NONE) |
| 10 | 不修改 v0.3 core contract | ✅(纯分析脚本,无代码改动) |
