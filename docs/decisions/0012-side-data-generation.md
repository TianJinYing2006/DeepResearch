# ADR 0012: Side-data request ownership

- Date: 2026-10-09
- Status: Accepted
- Requirement: [171](../requirements/171-side-data-generation.md)

## Context
Account cleanup resets state, but cannot prevent an earlier refresh from
restoring that state when its response arrives later.

## Decision
Each refresh captures a generation. Refresh, clear and unmount advance the
generation. Asynchronous state updates require the captured generation to match.

## Tradeoffs
The existing three requests remain concurrent. Responses from superseded
refreshes are ignored rather than used as a partial cache. Unlike abort-only
cleanup, generation checks also protect asynchronous response-body parsing.

## Change Log
| Date | Change |
| --- | --- |
| 2026-10-09 | Adopt request ownership for account side data |
