# SUB-001 Evidence Inventory(阶段一)

> 只做证据盘点,不判断 subagent 使用好坏。数据:56 个真实 DSH 会话。

---

## 一、完整盘点(15 个 descriptor)

| 指标 | 值 |
|---|---|
| subagent/descriptor 总数 | 15 |
| 含 descriptor 的会话 | 15(每个会话恰好 1 个) |
| version | 全部 v2 |
| mode | one-shot 12 / continuable 3 |
| provider | fork 6 / spawn 9 |

## 二、字段(descriptor 实际携带)

```python
descriptor.data = {
    "version": 2,
    "mode": "one-shot" | "continuable",
    "provider": "fork" | "spawn",
    "label": "任务描述(可空)",
    "agentModel": "(部分有)",
    "agentProvider": "(部分有)",
}
```

## 三、⭐ 关键发现(回答"topology 能否重建")

### 1. 真实 subagent delegation 全部是 flat

- 每个会话恰好 1 个 descriptor,**没有 fan-out、没有嵌套**;
- 这意味着当前数据里 **parent → subagent → subagent(nested) 的 topology 不存在**;
- 这本身是 empirical finding:DSH 的 subagent 使用模式是"一次一个 one-shot 任务"。

### 2. descriptor 不含生命周期

descriptor **没有**:
- parent id / subagent id
- outcome(success/failed)
- completion 事件
- related steps/events 关联

它只是"声明了一次 subagent 委托"的**描述性事件**,不是完整生命周期。

### 3. provider 语义

- `fork`:进程内 fork(共享内存,轻量)
- `spawn`:独立进程 spawn(隔离,重量)

## 四、SUB-001 第一版定位(数据驱动)

**observation,不是 defect,不是 token 归因:**

```
SUB-001: Subagent Delegation Observation
    Finding.kind = observation
    severity = info
    details:
        mode         (one-shot / continuable)
        provider     (fork / spawn)
        label        (任务描述)
        agent_model / agent_provider (若有)
        related_seq / related_time
```

**不能重建的(诚实声明)**:
- parent/subagent 关系(descriptor 无 parent id)
- outcome(无 completion 事件)
- nested topology(真实数据不存在)
- token cost(descriptor 无成本字段)

**Attribution**:kind=observation, tokens=None(无证据不做成本归因)。

## 五、成功标准对照(用户定的 10 条)

| # | 标准 | 现状 |
|---|---|---|
| 1 | 15 个 descriptor 稳定解析 | ✅ 可做 |
| 2 | lifecycle 重建 | ❌ 数据无 lifecycle 字段(诚实声明 unknown) |
| 3 | parent/subagent 关系不丢失 | ⚠️ descriptor 无 parent id,只能记录"会话级委托" |
| 4 | nested 不被 flatten | ✅ 真实数据无 nested(flat 是事实) |
| 5 | unknown 不猜 | ✅ outcome/parent 标 unknown |
| 6 | Finding.kind=observation | ✅ |
| 7 | Attribution.kind=observation | ✅ |
| 8 | tokens=None | ✅ |
| 9 | 不改核心 contract | ✅ |
| 10 | 接入 registry + CLI | ✅ |

## 六、诚实结论

SUB-001 在**当前真实数据**上只能做"delegation observation"——记录"这里发生了一次 subagent 委托,用了什么 mode/provider/label"。它**不能**回答:
- 这个 subagent 成功了吗(无 outcome)
- 它是谁派生的(无 parent)
- 它花了多少 token(无 cost 字段)

这些是**数据的边界**,不是实现的缺陷。未来若 DSH 丰富了 subagent 生命周期事件,SUB-001 可升级。当前版本的价值是:**把 subagent 委托模式(provider/mode/label)结构化观测出来**。
