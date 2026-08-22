# 外部评审报告 — token-invariant-check design.md (定稿 520 行)

> 评审模型: deepseek-v4-pro (异模型, 深度思考, 严格性优先)
> 评审日期: 2026-08-22
> 评审范围: design.md 全文 + proposal.md + PROJECT_STATE.md + context_health.py + 全部 6 个 detector 源码 + adapter 源码 + golden 基线构造器 + pipeline.py + report.py

---

## 硬约束红线逐条核对

| # | 约束 | 结论 | 证据 |
|---|------|------|------|
| 1 | additive: 不改现有 detector/attribution 行为; 默认输出逐字节不变 | **通过** | adapter 仅在解析末尾追加事件到 `trace.events[]`; 不修改 `Step.usage` / 遍历顺序 / schema; `DiagnosisResult.token_invariant` 仅在 `enable_analysis=True` 时填充; 报告渲染仅在该条件下追加块 |
| 2 | TokenInvariant 是分析层会话级数据块, 非 Detector/Finding, 不注册 ALL_DETECTORS/ALL_ATTRIBUTION_ENGINES | **通过** | design D0 明确; `build_token_invariant` 是纯函数, 挂在 `DiagnosisResult.token_invariant`; 与 `ContextHealth` 同构 |
| 3 | causal_claim=NONE; 不判 harness bug, 只报观测+风险; values 不一致单独建类 | **通过 (但报告措辞有稀释风险, 见 B/C)** | 设计明确 `token/usage-inconsistent` 独立事件类型; `inconsistent_usage_steps` 独立字段; 但报告渲染用全局 factor 表述风险, 见下文 |
| 4 | 禁混算 Total wasted tokens; 只能标非去重消费方的假设性溢出上界 | **通过** | `naive_double_count_tokens` 命名与注释严守语义边界; 报告块不出现 "wasted" |
| 5 | tokens=None ≠ 0; 没有证据不做成本归因 | **通过** | TokenInvariant 使用 int 0 表示"无双写 token", 语义为"零溢出"而非 "not applicable"; 不涉及归因 |
| 6 | findings 与 attribution 的 kind 解耦; 规则由真实数据决定 | **通过** | TokenInvariant 不进入 findings/attributions 体系 |
| 7 | 复杂系统只测局部封闭不变量, 设计权衡为 hedged 建议 | **通过** | `dedup_required` 明确标注 hedged; 只测同一 (turn,step) 的 usage 双写一致性 |

---

## 重点审查 (逐项)

### 【A 事件通道】Trace.events[] 是否真正 additive?

**结论: 保持, 但需补一个防御性断言。**

**理由 (逐条核实):**

1. **现有 6 个 detector 全部按 type 过滤 — 已验证源码:**
   - TOOL-001: 遍历 `trace.all_steps()`, 不接触 `events[]`。✅
   - CMP-001: 遍历 `trace.events`, 过滤 `ev.type not in COMPACTION_EVENTS` (4 个已知 type)。`token/usage-*` 不在集合内, 被跳过。✅
   - THINK-001: 遍历 `trace.all_steps()`, 不接触 `events[]`。✅
   - RETRY-001: 过滤 `e.type in ("llm/retry", "llm/retry-started")` 和 `e.type.startswith("llm/finish/")`。`token/usage-*` 不匹配。✅
   - SUB-001: 过滤 `ev.type != "subagent/descriptor"`。`token/usage-*` 被跳过。✅
   - TOOL-004: 遍历 steps 内的 tool_calls, 不接触 `events[]`。✅

2. **金钟罩 golden trace 不经过 adapter — 已验证源码:**
   - `build_comprehensive_trace()` 在 `tests/golden/golden_report.py` 中手动构造 `Trace`, 直接实例化 `Step` / `TraceEvent`, 不调用 `parse_dsh_jsonl()`。✅
   - Golden trace 的 `events[]` 仅含 `compaction/prune`、`llm/finish/*`、`llm/retry*`、`subagent/descriptor`。不含 `token/usage-*`。✅
   - 因此 `build_token_invariant()` 读取 golden trace 的 events[] 无双写事件 → 返回全零块。✅

3. **Adapter 注入位置正确:**
   - 新事件在解析末尾 (`return trace` 前) 生成, 不在 `STANDALONE_EVENT_TYPES` 集合中 (该集合用于原始 JSONL 事件路由, 合成事件不应进入)。✅

4. **发现的潜在问题:**
   - **`source_count > 2` 场景**: 设计代码 `_usage_equal(usages[0], usages[1])` 仅比较前两个来源。若同一 (turn,step) 有 3+ 个来源 (理论可能, 例如多个 chunk type=usage), 第三个及以后的来源被静默忽略。当前真实数据仅 2 来源, 但代码未防御。
   - **RETRY-001 的 `all_events = sorted(trace.events, key=lambda e: e.seq)`**: 该行创建了全量 events 排序副本但未使用 (实际使用 `retry_events` / `finish_events` 过滤后的列表)。`token/usage-*` 事件会进入该排序列表但不被消费, 无功能影响, 但属于死代码。

**是否需改: 是 (轻量)**

**具体改法:**
1. 在 `_usage_sources` 末尾事件生成处, 增加断言 `assert len(sources) == 2, f"Unexpected source_count={len(sources)} for (turn={tid}, step={sid})"`。若未来出现 3+ 来源, 断言失败可及时发现, 而非静默漏检。
2. 在 D1 表下方增加一条注释: "若未来新增 detector 遍历 `trace.events[]` 不做 type 过滤, 必须在该 detector 中显式排除 `token/usage-*` 前缀事件。"

---

### 【B 措辞/causal】报告块是否严格 causal_claim=NONE?

**结论: 需改 — 报告渲染存在语义稀释, 可能误导读者理解为全局统一乘数。**

**理由:**

`_render_token_invariant_block()` 的风险陈述 (L414-416):

```python
f"- **风险**:不按 (turn,step) 去重的消费方(朴素 chunk+message 求和)"
f"会精确 {ti.over_count_factor:.1f}× 高估 usage。"
```

当 `over_count_factor = 1.3` (部分双写) 时, 该陈述暗示"全局 1.3× 高估", 但实际语义是:
- 双写 step 精确 2× 高估
- 非双写 step 精确 1× (不高估)
- 全局 1.3× 是稀释后的平均值, 不是"精确"乘数

`{ti.over_count_factor:.1f}×` 的措辞 + "精确" 副词, 在部分双写场景下构成**事实性误导**: 非去重消费方不会"精确 1.3× 高估每个 step", 而是对双写 step 2×、对非双写 step 1×。

此外, L409 的 `{ti.over_count_factor:.2f}×` 精度与 L415 的 `.1f` 不一致。

**是否需改: 是 (阻塞级 — 语义正确性)**

**具体改法:**
```python
# 替换 L414-417 为:
if ti.over_count_factor >= 1.99:
    lines.append(
        f"- **风险**:不按 (turn,step) 去重的消费方(朴素 chunk+message 求和)"
        f"会精确 2× 高估全部 {ti.duplicate_usage_steps} 个双写 step 的 usage。"
    )
else:
    lines.append(
        f"- **风险**:不按 (turn,step) 去重的消费方会对 {ti.duplicate_usage_steps} 个"
        f"双写 step 精确 2× 高估 usage; 全局稀释后溢出倍数为 {ti.over_count_factor:.2f}×。"
    )
```

---

### 【C 溢出量算法】分母选择是否削弱诊断价值?

**结论: 需改 — 全局分母稀释了双写子集的真实信号, 缺少子集内因子。**

**理由:**

当前公式:
```
naive_double_count_tokens = Σ(每个双写 step 的 total_tokens)
over_count_factor = (total_deduped + naive_double) / total_deduped
```

问题分析:
- 假设会话有 10 个 step, 每个 step 100 tokens。其中 1 个 step 双写。
  - `total_deduped = 1000`
  - `naive_double = 100`
  - `over_count_factor = 1100/1000 = 1.10`
- 读者看到 `1.1×` 会认为"影响很小", 但双写 step 本身是 **2× 高估**。
- 全局分母将 9 个非双写 step 的 `1×` 混入, 稀释了唯一的双写 step 的 `2×` 信号。
- 设计 L124 声称"该因子仅限定在双写子集内解释", 但报告渲染未遵守此纪律 — 它把全局因子当作风险乘数直接呈现。

诊断价值评估:
- `over_count_factor` 作为**会话级摘要**有意义 (回答"这个会话整体溢出多少")
- 但它**不能替代**双写子集内因子 (永远 = 2.0), 后者才是诊断 "双写 step 被 2× 高估" 的核心信号
- 当前设计缺少 `double_write_subset_factor` 或等效的显式陈述 "双写 step 精确 2×"

**是否需改: 是 (阻塞级 — 诊断信号被稀释)**

**具体改法:**
1. `TokenInvariant` 新增字段 `double_write_step_count: int = 0` (双写 step 数, 语义同 `duplicate_usage_steps`, 但显式命名供报告使用)。
2. `TokenInvariant` 新增计算属性或字段 `double_write_multiplier: float = 2.0` (恒定 2.0, 明确双写子集内乘数)。
3. 报告渲染: 先报告"双写 step 精确 2× 高估", 再报告"全局稀释后溢出倍数 {factor}×"。
4. `over_count_factor` 文档注释补充: "全局稀释因子, 仅在双写占比高时接近 2.0; 诊断双写 step 风险应使用 `double_write_multiplier` (恒 2.0)。"

---

### 【D 阈值/边界】`_usage_equal` 与提取路径是否稳健?

**结论: 基本通过, 但需补文档说明已知局限。**

**理由:**

1. **`_usage_equal` 仅比较 4 个 key (`inputTokens`, `outputTokens`, `cacheReadTokens`, `reasoningTokens`):**
   - `cacheWriteTokens` 被排除的理由是 "Defined+Unobserved" (当前真实样本未见)。这是数据驱动的保守选择, 合理。
   - 但若未来 DSH 开始上报 `cacheWriteTokens`, 两份 usage 的该字段可能不同, 而 `_usage_equal` 会误判为一致 → 漏报 `token/usage-inconsistent`。
   - 这不是当前 bug, 但是**已知盲区**。

2. **`dict.get(k)` 对缺失 key 返回 `None`:**
   - 若两份 usage dict 结构不同 (例如一份有 `inputTokens` 另一份没有), `value == None` 会判为不一致 → 正确触发 `token/usage-inconsistent`。✅
   - 若两份 usage dict 都缺失同一 key, `None == None` → 判为一致。对于 `cacheReadTokens` 和 `reasoningTokens` 这是合理的 (两者都未上报 = 一致)。✅

3. **`message.usage` 提取路径:**
   - `msg = data.get("message", {})` → `msg_usage = msg.get("usage")` → `isinstance(msg_usage, dict)` 检查。✅
   - 路径 `assistant/message` → `data.message.usage` 与真实数据核验结果一致。✅

4. **不一致 step 不参与溢出计算:**
   - 正确。数值不一致时无法确定哪份是"正确"的, 不应纳入溢出上界。✅
   - 但需注意: `naive_double_count_tokens` 因此是**下界**而非上界 — 如果所有 step 都不一致, 溢出量为 0, 但实际风险未知。应在文档中标注。

**是否需改: 是 (轻量)**

**具体改法:**
1. `_usage_equal` 的 docstring 补充: "已知局限: 不比较 `cacheWriteTokens` (当前 Defined+Unobserved); 若 DSH 未来开始上报该字段, 需将其加入 `keys` 元组。"
2. `naive_double_count_tokens` 的 docstring 补充: "仅包含数值一致的重复 step; 不一致 step 被排除, 因此该值为下界而非上界。"
3. (可选) 在 `_usage_equal` 中增加 `cacheWriteTokens` 的比较, 但将其放在 `keys` 末尾并注释 "当前 Unobserved, 预留"。

---

### 【E 测试完备性】14 个用例是否覆盖充分?

**结论: 需补 4 个用例。**

**已覆盖:**
- 空 trace / 单来源 / 双写一致 / 双写不一致 ✅
- 全双写 factor=2.0 / 部分双写 1.0<factor<2.0 / 无双写 factor=1.0 ✅
- 不一致不参与溢出 / 确定性 / 非 Finding / enable_analysis 门控 ✅

**遗漏:**

| # | 遗漏场景 | 风险 | 优先级 |
|---|---------|------|--------|
| E1 | `source_count > 2` (3+ 来源同 (turn,step)) | `_usage_equal(usages[0], usages[1])` 忽略第三个来源, 可能漏检不一致 | 中 |
| E2 | `total_deduped_tokens == 0` (全部 step usage=0) | `over_count_factor` 除零保护 `if total_deduped > 0 else 1.0` 未测试 | 低 |
| E3 | `token/usage-duplicate` 事件的 `data.total_tokens` 值与 `Step.usage.total_tokens()` 一致性 | 若 adapter 的 `total_tokens` 计算与 `Usage.total_tokens()` 口径不同, 溢出量会偏差 | 中 |
| E4 | Golden trace + `enable_analysis=True` 时 `token_invariant` 为全零块 (非 None) | 确保金钟罩在分析模式下的行为确定 | 低 |

**是否需改: 是 (补 4 个用例)**

**具体改法:**
1. **E1**: 构造 trace 含 3 个 `token/usage-duplicate` 事件同 (turn,step), 验证 `build_token_invariant` 行为确定 (当前会按事件数计入 `duplicate_usage_steps=3`, 但 `naive_double` 会重复加总 — 需确认这是预期行为还是 bug)。
2. **E2**: 构造 trace 所有 step usage=0, 验证 `over_count_factor = 1.0` 且不抛异常。
3. **E3**: 构造 trace, 验证 `token/usage-duplicate` 事件的 `data.total_tokens` 等于对应 `Step.usage.total_tokens()`。
4. **E4**: 用 `build_comprehensive_trace()` 跑 `enable_analysis=True`, 验证 `token_invariant` 不为 None 且所有数值字段为 0/1.0/False。

---

## 附加发现 (不在 A-E 范围内但值得修复)

### F1: `_usage_equal` 在 `source_count > 2` 时仅比较前两个

design L175: `if _usage_equal(usages[0], usages[1])` — 仅比较前两个来源。若 sources 有 3 个元素, 第三个被忽略。若第三个与第一个不同, 会漏报 `token/usage-inconsistent`。

**建议**: 改为 `all(_usage_equal(usages[0], u) for u in usages[1:])`。

### F2: `_render_token_invariant_block` 中 `dedup_required` 的条件分支是死代码

L428-430:
```python
f"- **去重建议**:建议按 (turn,step) 去重"
f"{' (hedged 推荐,非无条件断言)' if ti.dedup_required else ''}"
```

该代码仅在 `ti.duplicate_usage_steps > 0` 时可达 (L383 有 early return), 此时 `dedup_required` 恒为 True。`else` 分支永远不执行。虽然功能正确, 但代码意图不清晰。

**建议**: 去掉条件, 直接写 `" (hedged 推荐, 非无条件断言)"`。

### F3: `over_count_factor` 精度不一致

L300: `round(factor, 4)` (4 位小数)
L409: `{ti.over_count_factor:.2f}×` (报告显示 2 位)
L415: `{ti.over_count_factor:.1f}×` (风险陈述显示 1 位)

三处精度不一致, 且报告渲染截断到 1 位小数可能将 `1.9999` 显示为 `2.0`, 触发全双写标签 `(全双写,朴素求和会 2× 高估)`。

**建议**: 统一精度。`over_count_factor` 存储用 4 位, 报告显示统一用 2 位。

---

## 总评

**设计整体守住了 7 条硬约束红线, 架构决策 (分析层数据块 / 不进 Finding 体系 / events[] 通道) 正确。但存在 2 个阻塞级问题必须修复后才能进入实现:**

### 阻塞项 (必改, 否则不可实现)

1. **【B/C 合并】报告渲染的风险陈述使用全局稀释因子 `over_count_factor` 作为乘数, 在部分双写场景下构成事实性误导。** 必须改为: 先陈述"双写 step 精确 2× 高估", 再陈述"全局稀释后溢出倍数 {factor}×"。具体改法见 B 和 C 的具体改法。

2. **【C】`TokenInvariant` 缺少双写子集内因子 (恒 2.0), 诊断信号被全局分母稀释。** 必须新增 `double_write_multiplier: float = 2.0` 字段或等效显式陈述。

### 建议改 (非阻塞, 但强烈建议)

3. **【E】补 4 个测试用例** (source_count>2, total_deduped=0, data 一致性, golden+enable_analysis)。
4. **【A】补 `source_count` 断言** 防御 3+ 来源场景。
5. **【F1】`_usage_equal` 改为 all-pairs 比较** 而非仅前两个。
6. **【D】补 `cacheWriteTokens` 已知盲区文档**。

### 非阻塞项 (可后续迭代)

7. 【F2】去重建议渲染的死代码清理。
8. 【F3】`over_count_factor` 精度统一。
9. 【D】`naive_double_count_tokens` 下界语义文档。

---

**最终判断: 设计不可直接进入实现。必须完成上述 2 个阻塞项 (B/C 报告措辞 + 双写子集因子) 的修复, 更新 design.md 后重新确认, 再交开发会话 `session-d2c507cf`。**

评审已写入 review-pro.md。