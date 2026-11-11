# Ticket rules for scout

Use these rules when an effort is given. Each file in `.scratch/<effort>/issues/` is one ticket.

1. A ticket matches when its first `Status:` line starts with `Status: resolved`. Match on that line alone. The `Type:` line does not matter: research, prototype, grilling and task tickets all match.
2. A qualified status counts, for example `Status: resolved (not yet confirmed with the user)`. Keep the qualifier. Name the ticket and its qualifier to the user before step 2.
3. For each matching ticket, put in `context`: its path, its `# ` title, its whole Status line, and everything from its `## Answer` heading to the end of the file. Include any `## Revision` and `## Comments` sections. Pass the full text, not a gist.
4. A matching ticket with no `## Answer` heading stops scout. Name the ticket to the user.
