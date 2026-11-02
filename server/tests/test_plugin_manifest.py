"""Phase 1 manifest checks (docs/phases/phase-1-scaffold.md, ".claude-plugin/plugin.json" and ".mcp.json").

`claude plugin validate .` is run separately; see the done criteria.
"""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_plugin_manifest_names_the_plugin():
    manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "navigator"
    assert manifest["version"] == "0.1.0"
    assert manifest["author"] == {"name": "Ben B"}


def test_mcp_json_launches_the_server_through_the_launcher():
    mcp = json.loads((REPO / ".mcp.json").read_text())
    entry = mcp["mcpServers"]["navigator"]
    assert entry["command"] == "${CLAUDE_PLUGIN_ROOT}/scripts/launch.sh"
    assert entry["args"] == []
    assert "timeout" not in entry  # main doc §9b: Claude Code's default applies
