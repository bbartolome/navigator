Type: grilling
Status: open
Blocked by:

# Ending a session from the admin process

## Question

How does the standalone admin process end a session safely while an MCP process may own it?

The MCP `navigator_end` checks for an active job in its own in-memory `JobManager` and holds an in-process
`threading.Lock`. The admin process has neither. Between the admin checking `jobs/<job_id>.pending.json` and writing
`state.json`, an owning MCP process can enqueue a job, or save a state it loaded earlier and overwrite `ended=true`.

Decide the mechanism (for example a cross-process file lock taken by both the MCP server and the admin, a
compare-and-swap on `state.json`, or an end request the owning process applies), what the admin does about
`drop_session` (forgetting jobs and deleting result files in the owner's memory), and when `end` is refused.
