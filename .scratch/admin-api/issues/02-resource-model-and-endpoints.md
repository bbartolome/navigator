Type: grilling
Status: open
Blocked by: 03

# Resource model and endpoint shapes

## Question

What resources and endpoints does the v1 contract expose, and what does each return?

- The list item for a session: which fields (mode, goal, `ended`, `phase`, current job, counts, metrics), and how
  age and ordering are derived given `SessionState` has no timestamps (directory or file mtime, a new field, or job
  file times).
- Whether view is one payload or split resources (state, transcript, jobs), and how the transcript is addressed
  (per ask N, per send, raw audit files or a normalised shape).
- Whether the transcripts of cancelled jobs, moved into `cancelled-<job_id>/`, are exposed.
- How corrupt (`SessionCorrupt`) or partially written sessions appear in the list.
- Filtering and pagination for the list, if any.
- The error shape and status codes, including the `end` refusals decided in ticket 03.
