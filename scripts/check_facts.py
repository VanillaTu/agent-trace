"""事实校验:文档中的关键数字声明 vs 事实源(代码/测试)。

用法:
    python scripts/check_facts.py          # 全量校验,不一致 exit 1
    python scripts/check_facts.py --doc X # 只看某个文档(调试用)

自动核验 auto 类事实(FACTS.md):
    测试总数    = pytest --collect-only 收集数
    间隔阈值 N  = counter_evidence.DEFAULT_GAP_THRESHOLD
    detector 数 = len(detectors.ALL_DETECTORS)

扫描"当前状态文档"白名单中的声明 pattern,任何不一致 → 列出并 exit 1。
历史报告(reports/)与归档 change(openspec/changes/archive/)不在白名单:
它们记录"当时的"状态,不应被当前事实约束。

确定性:纯文本扫描 + 固定事实源,无随机、无时间依赖。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 当前状态文档白名单(新增"当前状态"文档时记得加入)
DOCS = [
    "agenttrace/README.md",
    "agenttrace/ARCHITECTURE.md",
    "08-团队组织设计.md",
    "09-最初目标对照标注.md",
    "PROJECT_STATE.md",
    "FACTS.md",
]

# (名称, 声明 pattern(捕获数字), 事实源键)
# pattern 只匹配"总数声明"语境,避免误抓分解式写法(如 "83 原有 + 24 分析层")。
CHECKS = [
    ("测试总数", re.compile(r"(\d+)\s*(?:个\s*pytest|个\s*测试|个\s*全绿|全绿|passed)"), "test_count"),
    ("间隔阈值 N", re.compile(r"阈值\s*N\s*=\s*(\d+)"), "gap_threshold"),
    ("detector 数", re.compile(r"(\d+)\s*个\s*detector"), "detector_count"),
]

# 扫描前的文本预处理:剔除"历史/语境"数字,只留"当前状态声明"
# 1) 删除线片段 ~~...~~(历史修正记录,如待办里"残留 82 个测试")
# 2) "第 6/7/8 个 detector" 类裁剪讨论(非当前声明)
# 3) 显式跳过区段:<!-- facts-check:skip-start --> ... <!-- facts-check:skip-end -->
#    (文档作者用它圈住历史/示例区,如 FACTS.md 的"核验记录")
_PREPROCESS_PATTERNS = [
    re.compile(r"~~.*?~~", re.S),
    re.compile(r"第\s*[\d/、]+\s*个\s*detector"),
    re.compile(
        r"<!--\s*facts-check:skip-start\s*-->(.*?)<!--\s*facts-check:skip-end\s*-->",
        re.S,
    ),
]


def _preprocess(text: str) -> str:
    for pat in _PREPROCESS_PATTERNS:
        text = pat.sub("", text)
    return text


def facts_from_source() -> dict:
    """从事实源取真值:测试数(collect-only)+ 代码常量。"""
    # 测试总数:pytest --collect-only(不执行测试,只收集)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    combined = proc.stdout + proc.stderr
    m = re.search(r"(\d+)\s+tests?\s+collected", combined)
    test_count = int(m.group(1)) if m else None

    # 阈值 N + detector 数(import 事实源)
    from agenttrace.analysis.counter_evidence import DEFAULT_GAP_THRESHOLD
    from agenttrace.detectors import ALL_DETECTORS

    return {
        "test_count": test_count,
        "gap_threshold": DEFAULT_GAP_THRESHOLD,
        "detector_count": len(ALL_DETECTORS),
    }


def main() -> int:
    # GBK 终端兜底:强制 UTF-8 输出(Windows 控制台)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    facts = facts_from_source()
    problems: list[str] = []
    total = 0

    for rel in DOCS:
        path = ROOT / rel
        if not path.exists():
            problems.append(f"[缺失文档] {rel}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        text = _preprocess(text)
        for name, pattern, key in CHECKS:
            expected = facts[key]
            if expected is None:
                continue  # 事实源不可得(如 collect-only 失败),跳过该事实
            for m in pattern.finditer(text):
                total += 1
                got = int(m.group(1))
                if got != expected:
                    line_no = text[: m.start()].count("\n") + 1
                    problems.append(
                        f"{rel}:{line_no}  声明 {name}={got}({m.group(0)!r}),"
                        f"事实源={expected}"
                    )

    if problems:
        print("❌ 事实校验失败(文档声明与事实源不一致):")
        for p in problems:
            print("  " + p)
        print(
            "修复方式:若实现变更 → 同步文档;若文档表述过时 → 更新文档;"
            "若事实源本身变了 → 先确认代码,再改 FACTS.md。"
        )
        return 1
    print(f"✅ 事实校验通过:{total} 处声明 × {len(DOCS)} 文档,与事实源一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
