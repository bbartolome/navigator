import pytest


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    """Every test gets its own state root; CLAUDE_PLUGIN_DATA is unset so tests never touch the plugin data dir."""
    root = tmp_path / "sessions"
    monkeypatch.setenv("NAVIGATOR_STATE_DIR", str(root))
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    return root
