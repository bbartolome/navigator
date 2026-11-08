import pytest

# Provider and credential variables a developer may export in the shell (phase 7, plan D15).
_ENV_VARS = (
    "NAVIGATOR_PROVIDER",
    "NAVIGATOR_MODEL",
    "NAVIGATOR_OLLAMA_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_PROFILE",
    "ANTHROPIC_CONFIG_DIR",
    "ANTHROPIC_IDENTITY_TOKEN",
    "ANTHROPIC_IDENTITY_TOKEN_FILE",
    "ANTHROPIC_FEDERATION_RULE_ID",
    "ANTHROPIC_ORGANIZATION_ID",
    "ANTHROPIC_SERVICE_ACCOUNT_ID",
    "ANTHROPIC_WORKSPACE_ID",
    "ANTHROPIC_SCOPE",
    "ANTHROPIC_CUSTOM_HEADERS",
)


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    """Every test gets its own state root; CLAUDE_PLUGIN_DATA is unset so tests never touch the plugin data dir."""
    root = tmp_path / "sessions"
    monkeypatch.setenv("NAVIGATOR_STATE_DIR", str(root))
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    return root


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """No test sees the shell's provider or Anthropic credential variables. ANTHROPIC_CONFIG_DIR is never set."""
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """HOME is an empty directory, so the SDK finds no profile under ~/.config/anthropic."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def anthropic_env(monkeypatch):
    """A fake key and a base URL that only a mock transport answers. For tests that send through the SDK."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://anthropic.test")
