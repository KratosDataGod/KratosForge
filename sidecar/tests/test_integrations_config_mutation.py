"""integrations/config.py paths that mutation testing (batch 55, docs/quality/mutation-testing.md)
showed no test pinned: the launcher names on Windows and elsewhere, the default script form, a
launcher folder that doesn't exist yet, and the setup snippets' exact layout."""

import json
import os
from types import SimpleNamespace

from fabric_desktop.integrations import config

CMD = r"C:\Users\you\AppData\Local\FabricDesktop\bin\fabric-desktop-mcp.cmd"


def test_launcher_names_on_windows_and_elsewhere(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "os", SimpleNamespace(name="nt"))
    assert config.launcher_path(tmp_path) == tmp_path / "bin" / "fabric-desktop-mcp.cmd"
    assert config.cli_launcher_path(tmp_path) == tmp_path / "bin" / "kratosforge.cmd"
    monkeypatch.setattr(config, "os", SimpleNamespace(name="posix"))
    assert config.launcher_path(tmp_path) == tmp_path / "bin" / "fabric-desktop-mcp"
    assert config.cli_launcher_path(tmp_path) == tmp_path / "bin" / "kratosforge"


def test_the_script_form_follows_this_system_by_default(tmp_path):
    path = tmp_path / "Local" / "FabricDesktop" / "bin" / "kratosforge"
    assert config._script_text(path, "") == config._script_text(path, "", windows=os.name == "nt")


def test_a_launcher_folder_that_doesnt_exist_yet_is_made(tmp_path):
    root = tmp_path / "not" / "made" / "yet"
    path = config.write_cli_launcher(root)
    assert path == config.cli_launcher_path(root) and path.is_file()
    assert "-m fabric_desktop.cli " in path.read_text(encoding="utf-8")


def test_the_snippets_have_two_space_json_and_the_client_id():
    out = config.snippets({"id": "abc"}, CMD)
    entry = {"command": CMD, "args": ["--client", "abc"]}
    for key in ("claude-desktop", "cursor", "other-mcp"):
        text = out[key]["text"]
        assert json.loads(text) == {"mcpServers": {"fabric-desktop": entry}}, key
        assert text.splitlines()[1] == '  "mcpServers": {', key
    vscode = out["vscode"]["text"]
    assert json.loads(vscode) == {"servers": {"fabric-desktop": {"type": "stdio", **entry}}}
    assert vscode.splitlines()[1] == '  "servers": {'


def test_the_http_snippet_only_with_an_address():
    assert "http" not in config.snippets({"id": "abc"}, CMD)
    http = config.snippets({"id": "abc"}, CMD, "http://127.0.0.1:47615")["http"]
    assert http["label"] == "An HTTP client (REST API)"
    assert http["text"].startswith("Base URL: http://127.0.0.1:47615/api/v1\n")
    assert "MCP (Streamable HTTP): http://127.0.0.1:47615/mcp\n" in http["text"]
