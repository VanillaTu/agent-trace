"""dsh_adapter 测试:解压抽象 + 会话发现(分支会话改进)。

覆盖:
1. _decompress_zstd:zstandard 库解压 round-trip(内置路径)。
2. _default_sessions_root:DSH_SESSIONS_DIR 环境变量覆盖 > ~/.dsh/sessions 兜底。
3. discover_sessions:DSH 两种目录布局(根/<cwd组>/<session-id>/ 与 根/<session-id>/)的探测;
   缺根目录返回空;非会话目录跳过。
"""

from __future__ import annotations

from pathlib import Path

from agenttrace.adapters.dsh_adapter import (
    _decompress_zstd,
    _default_sessions_root,
    discover_sessions,
)


def test_default_sessions_root_env_override(tmp_path, monkeypatch):
    p = tmp_path / "fake-dsh"
    monkeypatch.setenv("DSH_SESSIONS_DIR", str(p))
    assert str(_default_sessions_root()) == str(p)


def test_default_sessions_root_fallback(monkeypatch):
    monkeypatch.delenv("DSH_SESSIONS_DIR", raising=False)
    assert _default_sessions_root().name == "sessions"


def test_decompress_zstd_roundtrip(tmp_path):
    """内置 zstandard 库解压 round-trip(库已装,走内置路径)。"""
    import zstandard

    src = tmp_path / "data.jsonl"
    src.write_text('{"a":1}\n{"b":2}\n', encoding="utf-8")
    zst = tmp_path / "data.jsonl.zstd"
    with zst.open("wb") as f:
        f.write(zstandard.ZstdCompressor().compress(src.read_bytes()))

    out = _decompress_zstd(zst)
    try:
        assert Path(out).read_text(encoding="utf-8") == '{"a":1}\n{"b":2}\n'
    finally:
        Path(out).unlink(missing_ok=True)


def test_discover_sessions_supported_layouts(tmp_path):
    """DSH 两种布局:根/<cwd组>/<session-id>/ 与 根/<session-id>/。"""
    root = tmp_path / "sessions"
    # 布局 1:根/<cwd组>/<session-id>/session.jsonl.zstd
    d1 = root / "--D-workspace--" / "session-a"
    d1.mkdir(parents=True)
    (d1 / "session.jsonl.zstd").write_bytes(b"x")
    # 布局 2:根/<session-id>/session.jsonl.zstd(无 cwd 组)
    d2 = root / "session-b"
    d2.mkdir(parents=True)
    (d2 / "session.jsonl.zstd").write_bytes(b"x")
    # 非会话目录:应跳过
    (root / "--D-code--" / "not-a-session").mkdir(parents=True)

    sessions = discover_sessions(str(root))
    ids = {s["session_id"] for s in sessions}
    assert "session-a" in ids
    assert "session-b" in ids
    assert "not-a-session" not in ids
    assert len(sessions) == 2
    # session_dir 指向含 jsonl 的会话目录(是目录,非文件)
    for s in sessions:
        p = Path(s["session_dir"])
        assert p.is_dir()
        assert (p / "session.jsonl.zstd").exists()
        assert s["has_zstd"] is True


def test_discover_sessions_missing_root(tmp_path):
    assert discover_sessions(str(tmp_path / "nope")) == []
