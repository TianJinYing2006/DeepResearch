# ADR 0014: Use terminal report availability in the workflow

- Date: 2026-10-09
- Status: Accepted
- Requirement: [173](../requirements/173-report-availability.md)

## Context
Terminal result objects preserve statistics even if report generation never
occurred. Moderation also removes text, although a report exists on the server.

## Decision
Prefer terminal `has_report`, with nonempty report text as a fallback when the
field is absent. Require a result object for the current report surface.
Use this fact for workflow completion and report actions; moderation separately
blocks actions. Continue rendering terminal statistics and the empty report state.

## Consequences
An empty interrupted run stops before report stages. A moderated report remains
at the review stage. Partial reports remain available after cancellation.

## Change Log
2026-10-09: Recorded report availability semantics from acceptance finding F3 (#173).
