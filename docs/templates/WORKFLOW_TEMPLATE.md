# Business workflow template

Status: canonical
Owner: project-maintainers
Last reviewed: 2026-09-03

Fill this in **without needing software architecture knowledge**. It
describes a business workflow the way an operator or the business owner
would explain it — the target architecture should eventually be derived from
these, not the other way around. See filled examples under
[`../workflows/`](../workflows/).

```markdown
# <Workflow name>

## Business purpose
Why this workflow exists — what business outcome it produces.

## Actor
Who does this — a role, not a system (e.g. "sales operator", "the business
owner", "a prospective customer"). If a machine does a step, say so in that
step, not here.

## Trigger
What starts this workflow (an event, a schedule, a decision).

## Required information
What must be known/true before this can start.

## Inputs
What comes in (a document, an email, a form, a signal from another
workflow).

## Human steps
What a person actually does, in order.

## System steps
What software does automatically, in order (interleave with human steps if
they alternate — order matters more than the human/system split).

## Decisions / gates
Points where a choice or approval is required, and what determines the
outcome. Note explicitly if a gate is a hard stop (fails closed) or a soft
warning.

## Outputs
What this workflow produces (a decision, a record, a document, a status
change).

## Documents generated
Any files/documents created (quotes, reports, exports).

## Communications generated
Any emails/messages sent as part of this workflow (or explicitly: none).

## Statuses visible to the operator
What an operator sees change as this workflow progresses.

## Completion condition
How you know this workflow is actually done.

## Exceptions
What can go wrong, and what happens then (not necessarily how the software
handles it today — note where today's handling is a gap).

## Evidence that should be retained
What proof/record should survive, even if the workflow is later rebuilt or
re-run.

## Data that must be durable
What must never be lost, even across a full rebuild of machine projections.

## Data that may be inferred / rebuilt
What can be safely regenerated from source evidence if lost.

## Unresolved questions
Anything about this workflow that isn't fully decided yet. It's fine — even
expected — for this section to be non-empty.
```
