# PunchOut Sandbox: notes for Claude Code

## Work tracking: Plane

This work lives in Plane, project **PSB** (PunchOut Sandbox) in the `weftworks` workspace,
https://plane.weftworks.co.uk. Claude reaches it through the `plane` MCP server.

- Starting a piece of work: find its item in PSB, or make one, and move it to In Progress.
- A PR opened: In Review. Merged, deployed or shipped: Done. Dropped: Cancelled.
- Found along the way and not today's job: a Backlog item with a label, not a TODO in the code.
- A new idea: PSB's Intake, not the backlog.
- Labels: feature, bug, chore, research, marketing, legal, finance, infra, customer-ask, blocked.
  A support ticket that needs work is customer-ask, titled with the ticket number and never the customer's name.
- Plane's API allows 60 calls a minute: read in pages, don't poll.
- No `plane` server in this session (a cloud session, say)? Carry on, and end with the Plane changes
  that are owed, so they can be made later.
