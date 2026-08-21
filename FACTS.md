# AgentTrace 事实清单(FACTS)

> 文档中引用的关键数字必须与代码/测试/真实数据一致,由 `scripts/check_facts.py` 自动核验(纯文本扫描,确定性,无随机)。
> 原则:**规则不是假设,而是由真实数据决定;数字不是摆设,而是可核验事实。**
> 分类:auto = 脚本可从事实源(代码/测试)自动取真值并扫描文档声明;manual = 依赖真实数据盘点,脚本只校验文档声明与当前值一致,改动需同步更新"最近核验"。

## Auto(脚本自动核验)

| 事实 | 当前值 | 事实源 | 文档声明 pattern |
|---|---|---|---|
| 测试总数 | 114 | `pytest --collect-only` | `N 个 pytest` / `N 全绿` / `N passed` |
| 间隔阈值 N | 5 | `counter_evidence.DEFAULT_GAP_THRESHOLD` | `阈值 N=N` |
| detector 数 | 5 个 detector | `detectors.ALL_DETECTORS` | `N 个 detector` |

## Manual(人工数据核验,改动需更新日期)

| 事实 | 当前值 | 数据来源 | 最近核验 |
|---|---|---|---|
| TOOL-001 gap 分布 | 中位数 13,min 2,max 252,24% ≤5 | 76 会话 / 131 条盘点(change#2 校准) | 2026-08-22 |
| THINK-001 分位 | P95=1498 / P99=3451 | 56 会话 / 2042 step(`reports/THINK001_baseline.md`) | 2026-08-20 |
| RETRY 失败 usage | 0(attribution boundary) | 56 会话 retry 盘点(`reports/RETRY001_evidence_inventory.md`) | 2026-08-20 |
| SUB-001 descriptor | flat,15 条,无 outcome/parent/cost | 56 会话盘点(`reports/SUB001_evidence_inventory.md`) | 2026-08-20 |

<!-- facts-check:skip-start -->
## 核验记录

- 2026-08-22 **107 → 114**:Pro 异模型评审修复 +7 测试(空 findings 渲染、缺证据保守×2、非法阈值、全 non-cost 排序、默认置信度守护、报告逐字节确定性),同步 ARCHITECTURE/09/README/08/PROJECT_STATE/FACTS 自身。此前残留:ARCHITECTURE 107、09 107、README 83、08 83/83、PROJECT_STATE 83/83+83 个 pytest(全部修正)。
<!-- facts-check:skip-end -->
