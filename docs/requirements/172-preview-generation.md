# Requirement 172: Keep chunk previews bound to their document

## 1. Metadata
| Field | Value |
| --- | --- |
| Issue | #172 |
| Priority | P2 |
| Status | Locally verified; merge status follows the linked GitHub PR |
| Date | 2026-10-09 |
| Branch | fix/172-preview-generation |
| Feishu mirror | Not synchronized in this GitHub-only task |

## 2. Background
An old preview request can overwrite a newer document's content or error.

## 3. Requirements
Open, close, account reset and unmount must invalidate obsolete responses.

## 4. Current Design
`useChunkPreview` sets its target immediately but writes asynchronous results
without checking that the same target still owns the response.

## 5. Solution
Use a generation ref and check ownership after fetch and body reads, including
error handling. Preserve immediate modal opening and its loading skeleton.

## 6. Design Decision
See [ADR 0013](../decisions/0013-preview-generation.md).

## 7. Acceptance
- [x] A delayed success, server error or parsing error cannot replace a newer preview.
- [x] Close, reset and unmount invalidate pending preview requests.
- [x] Production build, three browser regressions and 28 unit tests pass.
- [ ] All six PR checks pass before squash merge into dev.

## 8. Impact and Risk
Frontend preview ownership only; chunk API and display format are unchanged.

## 9. Tests
Deterministic browser tests hold A's response until after B has loaded.

## 10. Change Log
| Date | Change | Reference |
| --- | --- | --- |
| 2026-10-09 | Requirement recorded before implementation | #172 |
