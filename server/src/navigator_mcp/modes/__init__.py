"""Mode registry. Phase 4 registers pair and phase 5 registers scout."""

from ..ollama import Mode

MODES: dict[str, Mode] = {}


def get(name: str) -> Mode | None:
    return MODES.get(name)
