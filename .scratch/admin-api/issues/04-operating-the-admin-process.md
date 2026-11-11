Type: grilling
Status: open
Blocked by:

# Operating the admin process: port, token and start-up

## Question

How does an operator run the admin process and connect the UI to it?

- The default port value, and the CLI flags (if any) that mirror the env vars, following `--job-timeout`.
- The token lifecycle: generation, file permissions on `<state_root>/admin.token`, whether it is printed, rotation,
  and how the UI is given it.
- How the process is started where Claude Code runs in a container and the UI on the host (port publishing, bind
  host), and whether the plugin ships a launcher or only documents the command.
- What happens when `NAVIGATOR_STATE_DIR` is unset or points at an empty or wrong directory.
