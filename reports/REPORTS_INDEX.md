# REPORTS 索引

> 目的:把 `reports/` 散落的 evidence inventory / 诊断报告 / 评审 / 升级基线统一索引,
> 快速定位"某个 detector 的证据在哪、某次 E2E 的结论在哪、哪份报告是否含真实会话 ID"。
> 更新:2026-08-22

---

## 一、证据盘点 / Baseline(阶段一:规则由真实数据决定)

| 文件 | 用途 | 关键结论 | 敏感性 |
|---|---|---|---|
| `THINK001_baseline.md` | THINK-001 的 reasoning 分布 baseline(决定 P99/P95 阈值) | 56 会话 2042 step 的 reasoning 分布 | 无(统计) |
| `RETRY001_evidence_inventory.md` | RETRY-001 证据盘点 | 失败 attempt usage=0 → 无成本归因 | 无(统计) |
| `SUB001_evidence_inventory.md` | SUB-001 证据盘点 | 15 个 descriptor,flat 无 lifecycle | 无(统计) |
| `v04_correlation_inventory.md` | v0.4 共现分析 | SUB 与其他 rule 同 turn 零共现(负结论 lift≤1) | 无(统计) |

## 二、真实会话诊断报告(E2E / detector 抽查)

| 文件 | 用途 | 会话/模型 | 敏感性 |
|---|---|---|---|
| `e2e_unfamiliar.md` | 陌生 trace E2E | `session-f63ad704` / v4-flash | 含真实会话 ID |
| `e2e-session-d2c507cf.md` | 陌生 trace E2E(v4-pro) | `session-d2c507cf` / v4-pro | 含真实会话 ID |
| `session-d2c507cf-236e-...md` | 陌生 trace E2E(v4-flash) | `session-d2c507cf` / v4-flash | 含真实会话 ID |
| `post_fix_verify.md` | 归因 bug 修复后验证(1953→4249) | `session-<id>` / v4-pro | 含真实会话 ID |
| `cmp_real_session.md` | CMP-001 真实会话抽查 | `session-3ab9b08e` / qwen3-vl | 含真实会话 ID |
| `think_real_session.md` | THINK-001 真实会话抽查 | `ce78f433` / v4-flash | 含真实会话 ID |
| `retry_real_session.md` | RETRY-001 真实会话抽查 | `session-1491c2c7` / v4-flash | 含真实会话 ID |
| `sub_real_session.md` | SUB-001 真实会话抽查 | `05fc2d6b` / v4-flash | 含真实会话 ID |
| `v03_checkpoint_report.md` | v0.3 checkpoint 报告 | `session-3ab9b08e` / qwen3-vl | 含真实会话 ID |
| `full_checkpoint_report.md` | 早期完整报告(占位 id) | `session-<id>` | 占位 |

## 三、分析层 E2E(README 示例引用)

| 文件 | 用途 | 敏感性 |
|---|---|---|
| `e2e_analysis_on.md` | 分析层开启的五段式报告 | ✅ 已脱敏(README 示例) |
| `e2e_analysis_off.md` | 分析层关闭(默认路径)报告 | ✅ 已脱敏 |

## 四、外部评审 / 升级验证

| 文件 | 用途 | 备注 |
|---|---|---|
| `pro_review_change2_implementation.md` | Pro 异模型评审 change#2 分析层实现 | 复现 2 个边界 bug |
| `dsh_schema_fingerprint_0.1.0-rc.6.md` | 升级前 DSH schema 指纹基线 | 升级对照 |
| `dsh_schema_fingerprint_0.1.1-rc.2.md` | 升级后 DSH schema 指纹基线 | 唯一差异:parentSession/seedLength |

---

## 敏感性说明

- **已脱敏**:`e2e_analysis_on.md`、`e2e_analysis_off.md`(session-id 用占位)供 README 展示。
- **含真实会话 ID**:其余真实会话报告(session-xxx / UUID)。这些是本机会话 UUID,非个人敏感信息;若后续要把某份贴到公开 issue/帖子,需先脱敏。
- **统计类**:evidence inventory / baseline 均为聚合统计,无会话 ID。
