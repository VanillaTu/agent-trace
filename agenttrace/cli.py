"""AgentTrace CLI 入口。

用法:
    python -m agenttrace.cli analyze <session_dir> [--detector TOOL-001] [--out report.md]
    python -m agenttrace.cli diagnose <session_dir> [...]   # analyze 的别名
    python -m agenttrace.cli list-detectors
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _reconfigure_stdout_utf8():
    """GBK 终端下中文乱码的兜底:强制 stdout 用 UTF-8 输出。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _load_trace(session_dir: str):
    from .adapters.dsh_adapter import load_dsh_session
    return load_dsh_session(session_dir)


def cmd_analyze(args) -> int:
    trace = _load_trace(args.session_dir)

    from .detectors import ALL_DETECTORS
    names = [d.rule_id for d in ALL_DETECTORS]
    detector_names = args.detector.split(",") if args.detector else None
    if detector_names:
        unknown = [n for n in detector_names if n not in names]
        if unknown:
            print(f"未知 detector: {unknown}(可用: {names})", file=sys.stderr)
            return 2

    from .pipeline import diagnose
    result = diagnose(
        trace, detector_names=detector_names, enable_analysis=args.analysis
    )

    from .report import render_report
    report = render_report(
        result.trace,
        result.findings,
        result.attributions,
        enable_analysis=args.analysis,
        profile=result.profile,
    )

    if args.out:
        out = Path(args.out)
        out.write_text(report, encoding="utf-8")
        print(f"报告已保存: {out.resolve()}")
    else:
        print(report)

    if result.detector_errors:
        print(f"\n[detector errors] {result.detector_errors}", file=sys.stderr)
    if result.attribution_errors:
        print(f"\n[attribution errors] {result.attribution_errors}", file=sys.stderr)
    return 0


def cmd_list(args) -> int:
    from .detectors import ALL_DETECTORS
    from .attribution import ALL_ATTRIBUTION_ENGINES
    print("Detectors:")
    for d in ALL_DETECTORS:
        print(f"  {d.rule_id}  {d.__doc__.strip().splitlines()[0] if d.__doc__ else ''}")
    print("\nAttribution engines:")
    for k, v in ALL_ATTRIBUTION_ENGINES.items():
        print(f"  {k} → {v.__name__}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agenttrace", description="Agent Execution Trace Analyzer")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_analyze = sub.add_parser("analyze", help="对 DSH 会话目录跑完整诊断")
    p_analyze.add_argument("session_dir", help="包含 session.jsonl.zstd 的目录")
    p_analyze.add_argument("--detector", help="逗号分隔的 detector 名(默认全部)")
    p_analyze.add_argument("--out", help="输出 Markdown 报告路径")
    p_analyze.add_argument(
        "--analysis",
        action="store_true",
        help="开启分析层(反证 + 置信度完善 + 会话综合画像)",
    )
    p_analyze.set_defaults(func=cmd_analyze)

    p_diag = sub.add_parser("diagnose", help="analyze 的别名")
    p_diag.add_argument("session_dir")
    p_diag.add_argument("--detector", help="逗号分隔的 detector 名(默认全部)")
    p_diag.add_argument("--out", help="输出 Markdown 报告路径")
    p_diag.add_argument(
        "--analysis",
        action="store_true",
        help="开启分析层(反证 + 置信度完善 + 会话综合画像)",
    )
    p_diag.set_defaults(func=cmd_analyze)

    p_list = sub.add_parser("list-detectors", help="列出已注册的 detector")
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    _reconfigure_stdout_utf8()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
