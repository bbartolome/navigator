"""Mode registry: pair (phase 4) and scout (phase 5)."""

from ..ollama import Mode
from .pair import PAIR
from .scout import SCOUT

MODES: dict[str, Mode] = {"pair": PAIR, "scout": SCOUT}


def get(name: str) -> Mode | None:
    return MODES.get(name)
