# ADR 0015: Settle side-data fetch and body parsing together

- Date: 2026-10-09
- Status: Accepted
- Requirement: [174](../requirements/174-side-data-json-isolation.md)

## Context
Fetch resolving does not mean its response body is valid. Sequential body reads
outside allSettled allow one bad response to reject a void refresh call.

## Decision
Each promise includes fetch, HTTP handling and JSON parsing. Settle the three
complete promises, check generation ownership, then update each resource.
Document HTTP errors use the existing structured error reader; rejected network
or body reads use the existing network-error message.

## Consequences
Exactly three requests per refresh; successful resources survive other failures.
Account clear and newer refreshes invalidate all pending body reads as well.

## Change Log
2026-10-09: Recorded complete-operation isolation from acceptance finding F4 (#174).
