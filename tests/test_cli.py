"""`gsearch setup` prints a working MCP config without downloading anything when the browser is present."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from camoufox import pkgman
from gsearch_cli.main import main


def _config(out: str) -> dict[str, object]:
    config, _end = json.JSONDecoder().raw_decode(out, out.index("{"))
    return config  # type: ignore[no-any-return]


def test_setup_without_proxy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(pkgman, "camoufox_path", lambda download_if_missing=True: tmp_path)
    monkeypatch.delenv("GSEARCH_ENV_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    assert main(["setup"]) == 0
    out = capsys.readouterr().out
    server = _config(out)["mcpServers"]["gsearch"]  # type: ignore[index]
    assert "gsearch-mcp" in server["command"] and "env" not in server
    assert "claude mcp add gsearch --scope user -- " in out
    assert "your own IP" in out


def test_setup_points_clients_at_the_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(pkgman, "camoufox_path", lambda download_if_missing=True: tmp_path)
    monkeypatch.delenv("GSEARCH_ENV_FILE", raising=False)
    (tmp_path / ".env").write_text("GSEARCH_DATA_LIMIT_MB=10\n")
    monkeypatch.chdir(tmp_path)
    assert main(["setup"]) == 0
    out = capsys.readouterr().out
    env_file = str((tmp_path / ".env").resolve())
    assert _config(out)["mcpServers"]["gsearch"]["env"] == {"GSEARCH_ENV_FILE": env_file}  # type: ignore[index]
    assert f"-e GSEARCH_ENV_FILE={env_file} --" in out
    assert "your own IP" not in out
