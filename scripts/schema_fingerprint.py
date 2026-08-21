"""DSH schema 指纹:会话日志结构快照,用于 DSH 升级前后对比。

用法:
    python scripts/schema_fingerprint.py <session_dir> [tag]

输出 reports/dsh_schema_fingerprint_<tag>.md(不含会话 ID/内容,可进仓库)。

对比:DSH 升级后再跑一次(换 tag),diff 两份 md 即知日志格式是否变化
(顶层事件类型 / usage 字段 / tool call-result 结构等)。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path


def find_zstd() -> str | None:
    import shutil

    return (
        os.environ.get("ZSTD_PATH")
        or shutil.which("zstd")
        or os.path.expanduser(r"~/miniconda3/Library/bin/zstd.exe")
    )


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python scripts/schema_fingerprint.py <session_dir> [tag]")
        return 1
    session_dir = Path(sys.argv[1])
    tag = sys.argv[2] if len(sys.argv) > 2 else "unknown"

    zstd = find_zstd()
    if not zstd or not os.path.exists(zstd):
        print(f"zstd 未找到: {zstd}")
        return 1
    jsonl = session_dir / "session.jsonl.zstd"
    if not jsonl.exists():
        print(f"未找到 {jsonl}")
        return 1

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "session.jsonl"
        subprocess.run([zstd, "-d", "-f", str(jsonl), "-o", str(out)], check=True)

        top_types: Counter = Counter()      # 顶层 type
        chunk_types: Counter = Counter()    # assistant/chunk 的 chunk.type
        usage_fields: set = set()           # usage 对象键
        session_keys: set = set()           # session 行顶层键
        call_data_keys: set = set()         # tool/call 的 data 键
        result_data_keys: set = set()       # tool/result 的 data 键
        n_lines = 0
        with out.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                n_lines += 1
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = ev.get("type")
                if etype:
                    top_types[etype] += 1
                data = ev.get("data", {}) if isinstance(ev.get("data"), dict) else {}
                if etype == "session":
                    session_keys.update(ev.keys())
                elif etype == "assistant/chunk":
                    chunk = data.get("chunk", {}) if isinstance(data.get("chunk"), dict) else {}
                    ct = chunk.get("type")
                    if ct:
                        chunk_types[ct] += 1
                    if ct == "usage" and isinstance(chunk.get("usage"), dict):
                        usage_fields.update(chunk["usage"].keys())
                elif etype == "tool/call":
                    call_data_keys.update(data.keys())
                elif etype == "tool/result":
                    result_data_keys.update(data.keys())

    md = [
        f"# DSH schema 指纹(版本标签:{tag})",
        "",
        f"- 会话样本: 1 个",
        f"- JSONL 行数: {n_lines}",
        "",
        "## 顶层事件类型(type)",
        "",
        "| type | 次数 |",
        "|---|---|",
    ]
    for t, c in sorted(top_types.items()):
        md.append(f"| {t} | {c} |")
    md += ["", "## assistant/chunk 子类型(chunk.type)", ""]
    for t, c in sorted(chunk_types.items()):
        md.append(f"- {t}: {c}")
    md += ["", "## usage 字段", ""]
    md += [f"- `{k}`" for k in sorted(usage_fields)]
    md += ["", "## session 行顶层键", ""]
    md += [f"- `{k}`" for k in sorted(session_keys)]
    md += ["", "## tool/call data 键", ""]
    md += [f"- `{k}`" for k in sorted(call_data_keys)]
    md += ["", "## tool/result data 键", ""]
    md += [f"- `{k}`" for k in sorted(result_data_keys)]
    md.append("")

    out_md = Path("reports") / f"dsh_schema_fingerprint_{tag}.md"
    out_md.write_text("\n".join(md), encoding="utf-8")
    print(f"已生成 {out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
