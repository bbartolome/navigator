# Navigator

A Claude Code plugin where Claude drives and a local model navigates, reached through an MCP server that keeps the
session state.

## Language

**Navigator session**:
One pairing or scouting conversation with the navigator, identified by a session id and kept as one directory of
state and audit files.
_Avoid_: session (unqualified, when Claude Code sessions are also in play), conversation

**Job**:
One queued or running navigator ask or submit, owned by the MCP server process that accepted it.
_Avoid_: task (except for the MCP Tasks wire object), request

**Session log**:
The on-disk record of a navigator session: its state, the transcript of every ask, and its job history.
_Avoid_: logs (for server process output, which the server does not produce)

**Option**:
One choice the driver supplies when it asks the navigator to settle a question. The decision is one of the options,
or Other.
_Avoid_: choice, candidate (for the driver's side; the navigator's survey uses alternative)

**Alternative**:
One entry in the navigator's survey of a question: an option it judged, or the best one it found itself. Each carries a
case for and a case against.
_Avoid_: option (for the navigator's own findings)

**Other**:
The one decision available when no option fits. It carries the best alternative the navigator found.
_Avoid_: none-of-the-above, custom option

**Admin process**:
The standalone process that serves the admin API over the shared state directory, separate from any MCP server
process.
_Avoid_: admin server, dashboard backend
