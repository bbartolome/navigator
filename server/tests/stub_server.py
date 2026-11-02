"""Test-only launcher: the real navigator server with the phase 3 stub mode registered as 'pair'.

Phase 4 registers the real pair mode, after which test_stdio_tasks.py can use the plain navigator-mcp entry point.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fakes import StubMode  # noqa: E402

from navigator_mcp import modes, server  # noqa: E402

modes.MODES["pair"] = StubMode()

if __name__ == "__main__":
    server.main()
