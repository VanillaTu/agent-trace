# 检测盲区清单(Detector Blindspot Inventory)

> 来源:change `detector-blindspot-audit`(dogfooding 系统排查)
> 审计日期:2026-08-22
> 方法:对 10 个真实会话跑 `agenttrace analyze`,解压 JSONL 人工核对异常事件 ↔ 检出结果,找出"发生了但 5 detector 没检出"的模式

---

## 审计范围

| 会话 | 规模 | 检出结果 | 特点 |
|---|---|---|---|
| (主/讨论会话) | 96+ turns | — | **BL-001 样本**(send_session_message 缺 text 报错) |
| (开发会话) | 48t/488s/555c | TOOL×9, THINK×4, RETRY×1 | 长会话,RATE_LIMIT retry |
| (开发会话) | 39t/310s/345c | TOOL×3, RETRY×2 | 开发期 |
| (Pro 评审子代理) | 1t/5s/18c | THINK×2, SUB×1 | workflow 子代理 |
| (设计子代理) | 1t/18s/27c | THINK×2, SUB×1 | 设计 |
| (调研子代理) | 1t/33s/51c | THINK×1, SUB×1 | 调研 |
| (小样本会话) | 1t/2s/1c | SUB×1 | 小样本 |
| (失败子代理×4) | 1t/1s/**0c** | RETRY×1, SUB×1 | **失败子代理**(BL-003 样本×3) |

---

## 盲区清单

### BL-001: invalid param retry(无效参数重试) — 🔴 高优先级

**事件类型**:工具调用缺少必需参数 → 工具返回错误 → 下一步重试成功。

**真实样本**(完整证据链):
```
会话 <session-id>,turn 96 step 2:
  seq=270613  assistant/message   "开发会话在线(idle)。把 change#2 的实现任务完整投给它:"
  seq=270614  tool/call   call_00_ET_JGua8mmVEkJFpEFqa17Q1591   send_session_message(缺 text 参数)
  seq=270615  tool/result 同 callId → Error: invalid arguments: missing required property "text"
  seq=270616  step/end
  seq=270617  step/start   ← 下一步,agent 重新调用(带 text,成功)
```

**为什么现有 detector 检不出**:
- TOOL-001:两次调用参数不完全一致(第一次缺 text)→ 中间档,不判重复
- RETRY-001:只管 `llm/retry` 事件(模型层重试),不管工具调用失败后的重试

**对应最初设计**:05 文档 L558 `invalid param retry`(TOOL 因子,设计过未实现)。

**建议 detector**:`TOOL-004 invalid-param-retry`
- 检测:tool/result 为参数错误(error 文本含 invalid arguments / missing required / invalid_request)→ 标记该调用;同 callId 或相邻 step 有重试成功 → 归因"可避免的失败尝试"
- 思路:不估算成本(失败 attempt 无 usage),输出 observation/flag + 反证

**样本充足性**:✅ 充足(本会话 1 个 + 开发会话/其他会话可能还有;可再扫 `invalid arguments|missing required` 全库确认)

---

### BL-002: 探索性命令冗余(候选,样本不足) — 🟡 中优先级

**事件类型**:为定位问题连续跑多个一次性探索命令(read/list/grep/pwsh),可合并未合并。

**真实样本**:<session-id>(为找会话目录跑了 list + analyze 两步;主会话多处 grep/read 探索序列)。证据较弱——单命令本身正常,只有"序列冗余"才构成浪费,需要跨 step 关联分析。

**为什么现有 detector 检不出**:TOOL-001 需要"同工具同参数重复",探索命令参数都不同;且"序列冗余"需要上下文理解(规则难判)。

**对应最初设计**:05 L558 无直接对应(接近 orchestration/FLOW 类)。

**建议 detector**(暂缓):`ORCH-001 exploration-burst`(连续 N 个只读探索命令无产出 → flag)。**样本不足,待收集。**

---

### BL-003: subagent 零产出终止(委托后未执行) — 🔴 高优先级

**事件类型**:subagent/descriptor 声明了委托,但子代理会话 0 次工具调用就结束(模型重试失败或立即终止)。

**真实样本**(3 个):
```
<session-id> / <session-id> / <session-id>(三个连挂的子代理):
  事件分布:subagent/descriptor ×1, llm/retry ×2, turn/end ×1
  tool/call: 0  ← 从未执行任何工具
```

**为什么现有 detector 检不出**:
- SUB-001:只记录"发生了委托"(descriptor),不判断委托是否有产出
- RETRY-001:检出了"模型重试"(failed),但没揭示"整个子代理零产出"这个更本质的问题
- 需要**跨会话视角**(parent 的 descriptor + child session 的 tool 活动)才能判定——正是 v0.6 Cross-Session 的方向

**对应最初设计**:编排/拓扑类(05 L558 FLOW / SUB 类;v0.6 Cross-Session Lineage 相关)。

**建议 detector**:`SUB-002 subagent-zero-activity`(或并入 v0.6 跨会话分析)
- 检测:descriptor 存在但对应子会话 tool/call == 0 → 标记"委托未产生任何工具活动"
- 注意:需要 parent→child 关联(descriptor 无 parent id,按会话创建时间/上下文近似)——这是跨会话问题,单会话内无法可靠判定

**样本充足性**:✅ 充足(3 个同模式样本,同一天)

---

## 结论

### BL-004: 上下文压力与效率退化(上下文膨胀 → agent 行为退化) — 🔴 高优先级

**事件类型**:长会话中上下文膨胀导致 agent 效率退化——重复操作(重复解压/重复读取)、工作记忆错误(记错文件名)、用推测替代验证。

**真实样本**(dogfooding 现场,2026-08-22 审计过程中):
```
主会话 <session-id>(96+ turns, trace 12MB):
  - 同一 zstd 解压命令重复执行 4-5 次(TOOL-001 可检出,同命令同参数)
  - 文件名推算错误 2 次(把会话短 ID 文件名推算错)——未先 ls 验证
  - 临时文件复用导致误命中(scan.jsonl 残留)
  - 假设未验证就行动(先入为主 <session-id> 是主会话,走弯路)
```

**为什么现有 detector 检不出(部分)**:
- TOOL-001 **能**检出重复 zstd 调用(同命令同参数 4-5 次)——这是现成的验证样本
- 但"上下文过大 → 行为退化"这个**根因**没有指标:缺"会话上下文健康度"(tokens/窗口占比、轮次数、重复操作率)

**对应最初设计**:05 文档 CTX-001 历史膨胀(设计过未实现)。

**建议检测能力**:
- `CTX-001 context-health`:报告新增"上下文健康度"块——当前上下文 tokens/窗口占比、turn 数、重复操作率;超过阈值(如占用 >70%)标记"上下文压力高,建议压缩"
- 作为**统计标记(flag)**,不判因果(上下文大 ≠ 必然退化,但关联退化风险)

**样本充足性**:✅ 充足(主会话 96 turns/12MB + 本审计过程本身就是样本)

---

## 结论

| 盲区 | 优先级 | 样本 | 可立项? |
|---|---|---|---|
| BL-001 invalid param retry | 🔴 高 | ✅ 充足 | **可立项**(TOOL-004,单会话内可检测) |
| BL-002 探索命令冗余 | 🟡 中 | ⚠️ 不足 | 待收集样本 |
| BL-003 subagent 零产出 | 🔴 高 | ✅ 充足(3 样本) | **可立项但需跨会话**(建议并入 v0.6,或先做单会话近似版) |
| BL-004 上下文压力与退化 | 🔴 高 | ✅ 充足(主会话+审计过程) | **可立项**(CTX-001 context-health,单会话内统计标记) |

**立即建议**:BL-001 → 开 change 实现 TOOL-004(单会话内,最快);BL-004 → 开 change 实现 CTX-001 context-health(统计标记,单会话内,与 change#2 分析层互补);BL-003 → 与 v0.6 Cross-Session 一起规划。

---

## 方法备注

- 盲区判定标准:(a) trace 里可验证的异常/低效事件 (b) 不属于 5 个现有 detector 语义。
- 检索方式:zstd 解压 + grep 关键模式(error/invalid/missing required/tool 活动统计),未用浏览器。
- 人工核对,非自动;误判风险已通过"对应最初设计类别"锚定控制。
