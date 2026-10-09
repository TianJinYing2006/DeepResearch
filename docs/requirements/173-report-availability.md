# Requirement 173: Distinguish a result payload from an available report

## 1. Metadata
| Field | Value |
| --- | --- |
| Issue | #173 |
| Priority | P2 |
| Status | Locally verified; merge status follows the linked GitHub PR |
| Date | 2026-10-09 |
| Branch | fix/173-report-availability |
| Feishu mirror | Not synchronized in this GitHub-only task |

## 2. Background
Cancellation and timeout can return a result object with an empty report.
The workflow currently marks report, verification and export as completed.

## 3. Requirements
Use backend report availability, with a text fallback for older events. Keep
moderated reports visible as awaiting review and block their copy/export/share.

## 4. Current Design
`Boolean(result)` drives workflow completion; export only requires a run ID.

## 5. Solution
Derive report availability from `has_report` and result text. Use it for the
workflow, form collapse and report actions. Preserve empty-result statistics.

## 6. Design Decision
See [ADR 0014](../decisions/0014-report-availability.md).

## 7. Acceptance
- [x] Empty cancelled, timed out and completed runs cannot complete report stages.
- [x] Empty reports cannot copy, export or share.
- [x] Normal, partial and moderated reports retain their existing behavior.
- [x] Six browser tests, build and 28 unit tests pass.
- [ ] Six CI checks pass before merge.

## 8. Impact and Risk
Frontend interpretation of terminal events only. Moderation removes report text
but preserves `has_report`, so text alone cannot determine availability.

## 9. Tests
Browser fixtures use actual terminal SSE payload shapes for six scenarios.

## 10. Change Log
| Date | Change | Reference |
| --- | --- | --- |
| 2026-10-09 | Requirement recorded before implementation | #173 |
