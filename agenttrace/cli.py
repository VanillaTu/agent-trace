"""AgentTrace CLI 入口。

用法:
    python -m agenttrace.cli analyze <session_dir> [--detector TOOL-001] [--out report.md]
    python -m agenttrace.cli analyze --session-id <id> [--root <DSH sessions 根目录>] [--out report.md]
    python -m agenttrace.cli diagnose <session_dir> [...]   # analyze 的别名
    python -m agenttrace.cli list-detectors
    python -m agenttrace.cli list-sessions [--root <DSH sessions 根目录>]
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


def _resolve_session_dir(session_dir, session_id, root):
    """把 analyze/diagnose 的输入解析成实际 session 目录。

    返回 (session_dir, None) 或 (None, 错误信息)。
    支持:直接给目录;或给 --session-id 从 DSH 会话根目录解析。
    """
    if session_dir and session_id:
        return None, "参数冲突:不能同时给分析目录与 --session-id"
    if session_id:
        from .adapters.dsh_adapter import discover_sessions
        for s in discover_sessions(root):
            if s["session_id"] == session_id:
                return s["session_dir"], None
        return None, f"未找到会话 {session_id!r}(可用 list-sessions 查看)"
    if session_dir:
        return session_dir, None
    return None, "需要提供 session_dir 或 --session-id"


def cmd_analyze(args) -> int:
    session_dir, err = _resolve_session_dir(
        args.session_dir, getattr(args, "session_id", None), getattr(args, "root", None)
    )
    if err:
        print(err, file=sys.stderr)
        return 2
    trace = _load_trace(session_dir)

    from .detectors import ALL_DETECTORS
    names = [d.rule_id for d in ALL_DETECTORS]
    detector_names = args.detector.split(",") if args.detector else None
    if detector_names:
        unknown = [n for n in detector_names if n not in names]
        if unknown:
            print(f"未知 detector: {unknown}(可用: {names})", file=sys.stderr)
            return 2

    # A2:单会话局部 session_map(评审 C 方案2)——仅当前会话的 Trace,
    # 让跨会话 lineage 在默认 analyze 路径下至少部分可观测(own_* / parent_* 据 header)。
    session_map = {trace.session_id: trace}

    from .pipeline import diagnose
    result = diagnose(
        trace,
        detector_names=detector_names,
        enable_analysis=args.analysis,
        session_map=session_map,
    )

    from .report import render_report
    report = render_report(
        result.trace,
        result.findings,
        result.attributions,
        enable_analysis=args.analysis,
        profile=result.profile,
        context_health=result.context_health,
        token_invariant=result.token_invariant,
        session_lineage=result.session_lineage,
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


def cmd_list_sessions(args) -> int:
    """扫描 DSH 会话根目录(默认 ~/.dsh/sessions),列出可分析的会话。

    把 adapter 层的 discover_sessions 接入 CLI:不再只是死代码,
    让用户无需手输 session 目录即可看到本机有哪些可分析的会话。
    """
    from .adapters.dsh_adapter import _default_sessions_root, discover_sessions

    sessions = discover_sessions(args.root)
    display_root = args.root or str(_default_sessions_root())
    if not sessions:
        print(f"未发现可分析的 DSH 会话(根目录: {display_root})")
        return 0
    print(f"发现 {len(sessions)} 个 DSH 会话(根目录: {display_root})")
    print(f"  {'session_id':<48}has_zstd")
    for s in sessions:
        print(f"  {s['session_id']:<48}{s['has_zstd']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agenttrace", description="Agent Execution Trace Analyzer")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_analyze = sub.add_parser("analyze", help="对 DSH 会话跑完整诊断(目录或 --session-id)")
    p_analyze.add_argument("session_dir", nargs="?", help="包含 session.jsonl.zstd 的目录")
    p_analyze.add_argument("--session-id", help="从 DSH 会话根目录按会话 ID 解析(免手输目录)")
    p_analyze.add_argument("--root", default=None, help="DSH 会话根目录(配合 --session-id)")
    p_analyze.add_argument("--detector", help="逗号分隔的 detector 名(默认全部)")
    p_analyze.add_argument("--out", help="输出 Markdown 报告路径")
    p_analyze.add_argument(
        "--analysis",
        action="store_true",
        help="开启分析层(反证 + 置信度完善 + 会话综合画像)",
    )
    p_analyze.set_defaults(func=cmd_analyze)

    p_diag = sub.add_parser("diagnose", help="analyze 的别名")
    p_diag.add_argument("session_dir", nargs="?", help="包含 session.jsonl.zstd 的目录")
    p_diag.add_argument("--session-id", help="从 DSH 会话根目录按会话 ID 解析(免手输目录)")
    p_diag.add_argument("--root", default=None, help="DSH 会话根目录(配合 --session-id)")
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

    p_sess = sub.add_parser(
        "list-sessions",
        help="扫描 DSH 会话根目录,列出可分析的会话(analyze 取其一即可用)",
    )
    p_sess.add_argument(
        "--root",
        default=None,
        help="DSH 会话根目录(默认取 DSH_SESSIONS_DIR 环境变量,否则 ~/.dsh/sessions)",
    )
    p_sess.set_defaults(func=cmd_list_sessions)

    args = parser.parse_args(argv)
    _reconfigure_stdout_utf8()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
