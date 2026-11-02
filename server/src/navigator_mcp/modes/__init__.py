"""Mode registry: pair (phase 4). Scout registers here in phase 5."""

from ..ollama import Mode
from .pair import PAIR

MODES: dict[str, Mode] = {"pair": PAIR}


def get(name: str) -> Mode | None:
    return MODES.get(name)
