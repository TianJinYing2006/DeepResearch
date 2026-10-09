# Requirement 174: Isolate complete side-data requests

## 1. Metadata
| Field | Value |
| --- | --- |
| Issue | #174 |
| Priority | P2 |
| Status | Acceptance passed; final merge state follows [GitHub issue #174](https://github.com/TianJinYing2006/DeepResearch/issues/174) and its linked PR |
| Date | 2026-10-09 |
| Branch | fix/174-side-data-json-isolation |
| Feishu mirror | Not synchronized in this GitHub-only task |

## 2. Background
`allSettled` currently wraps fetch only. A successful HTTP response containing
malformed JSON rejects the refresh and prevents later resources from updating.

## 3. Requirements
Isolate network, HTTP and parsing failures per resource. Preserve request counts,
structured document errors and the generation ownership introduced by #171.

## 4. Current Design
Three concurrent fetches settle; their response bodies are parsed sequentially
outside the settled operations.

## 5. Solution
Settle complete fetch-and-parse promises, then apply the results only if their
generation remains current. Clear failed quota/usage and show document errors.

## 6. Design Decision
See [ADR 0015](../decisions/0015-side-data-json-isolation.md).

## 7. Acceptance
- [x] Malformed JSON for any resource cannot block successful other resources.
- [x] Refresh produces no unhandled rejection.
- [x] Structured document HTTP errors remain visible.
- [x] Generation regressions and all frontend checks pass: 80 browser tests, 28 unit tests, build and five guards.
- [ ] All six CI checks pass before merge.

## 8. Impact and Risk
Frontend side data only. A refresh waits for all three complete operations before
applying results, preserving concurrent requests and the latest-refresh policy.

## 9. Tests
Malformed quota/docs/usage browser tests and structured document error coverage,
plus #171 account races and the complete frontend suite.

## 10. Change Log
| Date | Change | Reference |
| --- | --- | --- |
| 2026-10-09 | Requirement recorded before implementation | #174 |
| 2026-10-09 | Complete browser suite passed; acceptance probes confirmed all four fixes | #171, #172, #173, #174 |
