# ADR 0013: Invalidate obsolete preview responses

- Date: 2026-10-09
- Status: Accepted
- Requirement: [172](../requirements/172-preview-generation.md)

## Context
The preview opens before fetching chunks so the user immediately sees its title
and skeleton. A response can arrive after close, reset or another open.

## Decision
Increment a generation on open, close, reset and unmount. Only the current
generation may write chunks, counts or errors, including after body parsing.

## Consequences
Keep the existing immediate-open behavior. Requests may finish in the
background, but their responses cannot update another document's preview.
Transport cancellation alone would not guard already started body parsing.

## Change Log
2026-10-09: Recorded the acceptance finding and response ownership decision (#172).
