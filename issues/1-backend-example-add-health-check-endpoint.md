---
number: 1
area: backend
slug: example-add-health-check-endpoint
status: draft
priority: medium
requires_plan: false
attempts: 0
branch: null
completed_at: null
---

## Description

Example ticket — delete or replace it. Tickets start as `draft`; set
`status: ready` when the description is complete and the agent should pick
it up.

Add a `GET /health` endpoint that returns `{"status": "ok"}` with a 200, plus
a test covering it.
