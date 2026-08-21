"""CLI 层测试:验证 list-sessions 子命令把 adapter 的 discover_sessions 接进 CLI。

覆盖两类 DSH 目录布局(根/<cwd组>/<session-id>/ 与 根/<session-id>/),
以及根目录不存在时的空结果。
"""

import pytest

from agenttrace.cli import cmd_list_sessions


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
