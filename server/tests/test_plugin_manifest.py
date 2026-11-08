"""Phase 1 manifest checks (docs/phases/phase-1-scaffold.md, ".claude-plugin/plugin.json" and ".mcp.json").

`claude plugin validate .` is run separately; see the done criteria.
"""

import json
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_plugin_manifest_names_the_plugin():
    manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "navigator"
    assert manifest["version"] == "0.1.2"
    assert manifest["author"] == {"name": "Ben B"}


def test_server_package_version_matches_the_plugin_version():
    # scripts/launch.sh reinstalls the server only when pyproject.toml changes, so a rebuild needs this bump.
    manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
    pyproject = tomllib.loads((REPO / "server" / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == manifest["version"]


def test_mcp_json_launches_the_server_through_the_launcher():
    mcp = json.loads((REPO / "mcp" / "navigator.json").read_text())
    entry = mcp["mcpServers"]["navigator"]
    assert entry["command"] == "${CLAUDE_PLUGIN_ROOT}/scripts/launch.sh"
    assert entry["args"] == []
    assert "timeout" not in entry  # main doc §9b: Claude Code's default applies


def test_mcp_json_pins_the_uv_version_the_launcher_installs():
    mcp = json.loads((REPO / "mcp" / "navigator.json").read_text())
    env = mcp["mcpServers"]["navigator"]["env"]
    assert env["NAVIGATOR_UV_VERSION"] == "0.13.6"  # matches the uv_build pin in server/pyproject.toml


def test_plugin_manifest_points_at_the_server_config():
    manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["mcpServers"] == "./mcp/navigator.json"


def test_no_project_mcp_json_at_the_repo_root():
    # A root .mcp.json is also read as a project server, which duplicates the plugin's server.
    assert not (REPO / ".mcp.json").exists()


def test_launcher_defaults_the_uv_version_so_the_pin_is_not_required():
    launcher = (REPO / "scripts" / "launch.sh").read_text()
    assert "NAVIGATOR_UV_VERSION:-0.13.6" in launcher
