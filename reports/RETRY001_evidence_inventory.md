# RETRY-001 Evidence Inventory(阶段一)

> 只做证据盘点,不写 detector 规则。数据:56 个真实 DSH 会话。

---

## 一、基础统计

| 指标 | 值 |
|---|---|
| llm/retry 总数 | 13 |
| 含 retry 的会话 | 7 / 56 |
| distinct retryId | 6 |
| 每个 retryId 平均 retry 次数 | 2.17 |
| 单次 retry(仅 retry 1 次) | 1 |
| 多次 retry(retry >1 次) | 5 |
| 每 step 最多 retry | 4 |

## 二、分布

### provider
- ollama: 8
- volcengine-ark: 5

### mode
- normal: 13(全部)

### error code(出现的失败原因)
| code | 次数 |
|---|---|
| TRANSPORT | 13 |
| RATE_LIMIT | 6 |
| CONTEXT_WINDOW_EXCEEDED | 4 |
| INVALID_REQUEST | 2 |
| PI_AI_ERROR | 2 |

## 三、retry 生命周期

真实结构(已确认):

```
assistant/chunk (usage=0)        ← 失败的尝试,无 token 消耗
    ↓
assistant/chunk (finish error)   ← reason.kind=error, failure.{code,message}
    ↓
llm/retry {retryId, provider, mode, policyKey}
    ↓
llm/retry-started {retryId, retry: N}
    ↓
assistant/chunk (usage=0)        ← 下一尝试
    ↓ ... 循环 ...
```

### 生命周期详情(每 retryId)
| retryId | retry 次数 | error code | 最终 outcome |
|---|---|---|---|
| <retry-id> | 2 | RATE_LIMIT(429 AccountQuotaExceeded) | **failed** |
| <retry-id> | 2 | TRANSPORT(Connection error) | **failed** |
| <retry-id> | 4 | TRANSPORT ×4 | **failed** |
| <retry-id> | 2 | TRANSPORT ×2 | **failed** |
| <retry-id> | 2 | RATE_LIMIT ×2 | **failed** |
| <retry-id> | 1 | (recovered) | **recovered**(usage 266/683) |

## 四、⭐ 关键发现(回答"retry 是事件还是成本?")

### 1. 失败的 retry attempt usage = 0(无 token 消耗)

5/6 retryId 最终失败,其所有 attempt 的 `assistant/chunk usage = (0,0)`。
**结论:在当前数据里,"retry 浪费 token"不成立——失败的尝试在 error 前就中断,未产生计费 token。**

### 2. 只有成功恢复的 retry 才有 usage(<retry-id>: 266/683)

retry 后**成功**的 attempt 才产生 token。这意味着:
- retry 本身不是直接 token 成本;
- retry 的"成本"体现在:失败尝试占用的**时间/延迟** + 成功前的**等待**,而非 token。

### 3. retry 的语义边界

| 情形 | 数据证据 | 结论 |
|---|---|---|
| 失败尝试 | usage=0 | **observation**(发生了 retry,无直接 token 成本) |
| 恢复尝试 | usage>0 | 可归因(但这是"重试后成功的正常调用",非浪费) |
| 最终失败 | 无 usage | observation(该 step 未产生输出 → 可能产生"缺失输出"的间接成本) |

## 五、RETRY-001 定位建议(数据驱动)

**第一版定位:observation(不是 cost)**

```
RETRY-001: Model Retry Event
    kind = observation
    details:
        retry_id
        provider
        mode
        error_code        (TRANSPORT/RATE_LIMIT/...)
        retry_count       (同一 retryId 的重试次数)
        outcome           (recovered / failed)
        usage_after       (恢复后的 usage,若有)
```

**为什么不声明 cost(诚实结论)**:
- 数据证明失败的 retry usage=0,没有直接 token 浪费可归因;
- 若硬算 "retry × 某 token 单价" 是**虚构成本**,违背项目铁律;
- retry 的真正代价是**延迟/可靠性**,不是 token——这本身是有价值的观测发现。

**可选的间接归因(保守,第二版)**:
- 若 retry 最终失败且 step 无输出 → 标记 "step 无产出"(可能影响任务完成),但不关联 token cost;
- 若 retry 恢复 → 该 step 的 usage 是"正常成本",不视为浪费。

## 六、对比:为什么 RETRY 和 TOOL/CMP/THINK 不同

| | 数据证据强度 | 成本可归因性 | 定位 |
|---|---|---|---|
| TOOL-001 | 高(重复调用明确) | ✅ 可归因(extra output) | cost defect |
| CMP-001 | 高(shadowedTokenCount 硬证据) | 观测(不声明 avoidable) | hard observation |
| THINK-001 | 中(统计强度) | 观测(不声明 avoidable) | statistical flag |
| **RETRY-001** | 高(事件明确) | **不可归因(usage=0)** | **observation(可靠性信号)** |

**RETRY-001 的价值不在"省 token",而在"诊断可靠性问题"(TRANSPORT/RATE_LIMIT 持续失败 → provider 或配额问题)。**
