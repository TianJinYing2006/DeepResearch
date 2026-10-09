# Requirement 171: Invalidate stale account side data

## 1. Metadata
| Field | Value |
| --- | --- |
| Issue | #171 |
| Priority | P1 |
| Status | Locally verified; merge status follows the linked GitHub PR |
| Date | 2026-10-09 |
| Branch | fix/171-side-data-generation |
| Feishu mirror | Not synchronized in this GitHub-only task |

## 2. Background
Acceptance testing held an account's document response until after logout.
The late response restored documents that account cleanup had already removed.

## 3. Requirements
Cleanup and subsequent refreshes must invalidate older requests. Each refresh
must retain exactly three requests and preserve successful response behavior.

## 4. Current Design
`web/frontend/src/hooks/useSideData.ts` resets local state but does not identify
which refresh owns later asynchronous updates.

## 5. Solution
Assign each refresh a monotonically increasing generation. Clear and unmount
invalidate the generation. Check ownership after every asynchronous body read.

## 6. Design Decision
See ADR 0012. A generation guard also handles completed requests whose body
parsing remains pending; aborting transport alone would not establish ownership.

## 7. Acceptance
- [x] Logout prevents late data from returning.
- [x] A newer refresh wins over an older response.
- [x] Browser regressions and CLI checks pass: 2 browser tests, 28 unit tests, build and guards.
- [ ] All PR checks pass before squash merge into dev.

## 8. Impact and Risk
Only frontend side-data ownership changes. No API or research-engine behavior
changes. Latest refresh wins; superseded responses are deliberately ignored.

## 9. Tests
`npm run e2e -- e2e/side-data-race.spec.ts`, production build and frontend guards.

## 10. Change Log
| Date | Change | Reference |
| --- | --- | --- |
| 2026-10-09 | Requirement created from acceptance finding F1 | #171 |
