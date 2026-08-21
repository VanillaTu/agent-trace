"""CLI 层测试:验证 list-sessions 子命令把 adapter 的 discover_sessions 接进 CLI。

覆盖两类 DSH 目录布局(根/<cwd组>/<session-id>/ 与 根/<session-id>/),
以及根目录不存在时的空结果。
"""

import pytest

from agenttrace.cli import _resolve_session_dir, cmd_list_sessions, main


def _make_layout(root):
    """构造两种 DSH 目录布局并返回发现结果的期望。"""
    # 布局 1:根/<session-id>/session.jsonl.zstd(无 cwd 组)
    (root / "alpha" / "session.jsonl.zstd").parent.mkdir(parents=True)
    (root / "alpha" / "session.jsonl.zstd").write_bytes(b"x")
    # 布局 2:根/<cwd 组>/<session-id>/session.jsonl.zstd
    (root / "cwd-group-1" / "beta" / "session.jsonl.zstd").parent.mkdir(parents=True)
    (root / "cwd-group-1" / "beta" / "session.jsonl.zstd").write_bytes(b"x")
    # 干扰项:没有 zstd 的目录,应被忽略
    (root / "no-zstd" / "session.jsonl").parent.mkdir(parents=True)
    (root / "no-zstd" / "session.jsonl").write_bytes(b"x")


class Args:
    """最小 args 桩,带 cmd_list_sessions 需要的 root 属性。"""

    def __init__(self, root):
        self.root = root


def test_list_sessions_discovers_both_layouts(tmp_path, capsys):
    root = tmp_path / "sessions"
    _make_layout(root)

    rc = cmd_list_sessions(Args(str(root)))
    out = capsys.readouterr().out
    assert rc == 0
    assert "发现 2 个 DSH 会话" in out
    assert "alpha" in out
    assert "beta" in out
    # no-zstd 目录未被当成会话
    assert "no-zstd" not in out


def test_list_sessions_missing_root(tmp_path, capsys):
    root = tmp_path / "nope"
    rc = cmd_list_sessions(Args(str(root)))
    out = capsys.readouterr().out
    assert rc == 0
    assert "未发现" in out


# ---- _resolve_session_dir:analyze/diagnose 的 --session-id 解析 ----
# 覆盖:直接给目录、两者冲突、按 ID 解析、ID 未找到、两者都缺


def test_resolve_direct_dir(tmp_path):
    d, err = _resolve_session_dir(str(tmp_path / "sess"), None, None)
    assert err is None
    assert d == str(tmp_path / "sess")


def test_resolve_conflict(tmp_path):
    d, err = _resolve_session_dir(str(tmp_path / "sess"), "abc", None)
    assert d is None
    assert "冲突" in err


def test_resolve_by_session_id(tmp_path):
    root = tmp_path / "sessions"
    _make_layout(root)  # alpha 与 beta 两个会话
    d, err = _resolve_session_dir(None, "beta", str(root))
    assert err is None
    assert d.endswith("beta")


def test_resolve_session_id_not_found(tmp_path):
    root = tmp_path / "sessions"
    _make_layout(root)
    d, err = _resolve_session_dir(None, "gamma", str(root))
    assert d is None
    assert "未找到会话" in err


def test_resolve_neither():
    d, err = _resolve_session_dir(None, None, None)
    assert d is None
    assert "需要提供" in err


def test_main_analyze_session_id_wire(tmp_path):
    # 走 argparse 全链路:--session-id 解析失败应返回 2(而非误当目录解析)
    rc = main(["analyze", "--session-id", "gamma", "--root", str(tmp_path), "--out", "x.md"])
    assert rc == 2
