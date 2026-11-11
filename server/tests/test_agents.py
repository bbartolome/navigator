"""Subagent drivers: agents/pair-driver.md and agents/scout-driver.md.

Claude Code cannot preload a skill whose frontmatter sets disable-model-invocation: true, and the pair and
scout skills must stay user-only. So each agent reads its skill's SKILL.md at run time and overrides only what a
subagent cannot do. These tests pin that contract.
"""

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
AGENTS = {
    "pair-driver": ("pair", REPO / "agents" / "pair-driver.md"),
    "scout-driver": ("scout", REPO / "agents" / "scout-driver.md"),
}
NAVIGATOR_TOOLS = [
    "navigator_start",
    "navigator_ask",
    "navigator_submit",
    "navigator_record",
    "navigator_status",
    "navigator_cancel",
    "navigator_end",
]


def _split(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    head, body = text[4:].split("\n---\n", 1)
    return yaml.safe_load(head), body


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_agent_frontmatter(agent):
    meta, _ = _split(AGENTS[agent][1])
    assert meta["name"] == agent
    assert meta["description"].strip()
    # Plugin agents must not set these; the loader ignores them and the docs say so.
    for field in ("hooks", "mcpServers", "permissionMode", "initialPrompt"):
        assert field not in meta, field
    # A skill with disable-model-invocation cannot be preloaded, so the agent must not rely on `skills:`.
    assert "skills" not in meta


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_agent_reads_the_skill_it_drives(agent):
    skill, path = AGENTS[agent]
    _, body = _split(path)
    assert f"${{CLAUDE_PLUGIN_ROOT}}/skills/{skill}/SKILL.md" in body
    assert (REPO / "skills" / skill / "SKILL.md").is_file()


@pytest.mark.parametrize("agent", sorted(AGENTS))
def test_agent_overrides_cover_what_a_subagent_cannot_do(agent):
    _, path = AGENTS[agent]
    _, body = _split(path)
    # AskUserQuestion is unavailable in Agent-tool subagents (Agent SDK user-input docs, Limitations).
    assert "AskUserQuestion is not available" in body
    assert "Needs user:" in body
    # Subagent navigator calls are not auto-backgrounded, so wait_command runs in the foreground.
    assert "run it with Bash in the foreground" in body
    assert "timeout 600000" in body
    # Deferred MCP tools need ToolSearch before a direct call.
    assert "ToolSearch" in body
    for tool in NAVIGATOR_TOOLS:
        assert f"mcp__plugin_navigator_navigator__{tool}" in body, tool


def test_scout_driver_does_not_see_the_parent_conversation():
    _, body = _split(AGENTS["scout-driver"][1])
    assert "cannot see the parent's conversation" in body
    assert "from this conversation, not recorded on disk" in body


def test_scout_driver_keeps_the_approval_prompt_with_the_parent():
    _, body = _split(AGENTS["scout-driver"][1])
    assert "do not call `navigator_end` before the Approve / Reject / Other question" in body


@pytest.mark.parametrize("skill", ["pair", "scout"])
def test_skills_stay_user_only(skill):
    # Agents exist so subagents can run these; the skills themselves must not become model-invocable.
    text = (REPO / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    assert "disable-model-invocation: true" in text
