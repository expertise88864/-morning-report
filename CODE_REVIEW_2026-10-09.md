# Pending Review Follow-Up

Base: `e1a257c7c7bfdfc5f10da38c0dbcf6cf2f3f94ba`.
Reviewer: `claude-opus-5-5`, effort `high`, read-only, exact-commit scope.

The initial queue contained 43 commits (40 main ancestors, three branch-only).
42 received verified model verdicts: 19 APPROVE and 23 REQUEST_CHANGES.
The remaining commit, `9ebbcf84691f782d310f3632facf38b31fb8dc3b`, encountered
provider quota; the provider reported a 20:30 Asia/Taipei reset. A follow-up
heartbeat is scheduled. The repairs themselves still require independent review.
Neither local tests nor this document constitute that approval or remote CI.

## Findings Reconciled Against Current Code

Historical REQUEST_CHANGES verdicts remain unchanged. A later repair does not
retroactively approve an old commit. Short IDs below resolve in this repository.

| Commit | Disposition |
| --- | --- |
| 58c14b2 | Fixed current-cycle news contaminating historical/background evidence; retain genuinely prior observations. |
| bc32299 | Fixed exact HTTPS repository identity with optional `.git`, and bounded retries of transient CI evidence retrieval failures. |
| 8e3a54b | Same delivery URL fix; obsolete reviewer model documentation already corrected at HEAD. |
| 2bbb14a | Dismissal coverage already corrected at HEAD; separate finance interaction addressed below. |
| 53fea9b | Restrict labelled Markdown links to collected source addresses, including the minimal renderer. |
| ee53685 | Remove unrelated finance obligations from the single-dismissal validation probe, not from whole-report validation. |
| 0f3ff81 | Branch-only finding: fix current short-style compaction using actual byte savings; preserve existing classes. |
| 440f8b9 | Outlook destination and missing-section handling already corrected at HEAD. |
| 159e214 | Stop ordinary English words such as now/cost/net from becoming stock headings. |
| 8898c2f | Suppress aggregate selection-study comparisons when selected exit prices are missing; preserve the frozen prospective protocol. |
| 577be24 | Deadline-first review selection already corrected at HEAD. |
| d8239c6 | Fix finance probe interaction and routine finance vocabulary; distinguish expiries without an intervening session using known session evidence. |
| 2376e16 | User explicitly retains MOPS ranking participation. Cross-issuer merging did not reproduce; add regression proving separate issuer clusters. No ranking weight change. |
| 04a1bc7 | Preserve source HTML block boundaries before instruction sanitization, including historical reflow. |
| e6397f0 | Include news ID and comparison location in Podcast validation/repair failures, preserving exact excerpt requirements. |
| 9e8facd | Emit Podcast degradation annotations without a logging prefix; retain summaries when quotation/direction evidence fails. |
| f12dbac | Require evidence IDs for new negative watch results in prompt/schema/validator; never infer partial success from a negative declaration. |
| 6871274 | Remove unsupported stance-continuity wording; reject contrary signal-only conclusions without a matching overall stance. |
| 48d4244 | Narrow routine finance vocabulary while retaining substantive capital plans and regulatory changes; test selection under budget pressure. |
| 532a468 | Accept the existing issuer's distinctive short name, 世芯, at company-news ingress. |
| 7b8b7a8 | Date-known/time-unconfirmed event visibility already corrected at HEAD; existing offline regressions pass. |
| f0e1b2b | Preserve tennis result headlines that contain preview words alongside completion/outcome wording. |
| a192333 | Deeply nested JSON recovery already catches RecursionError at HEAD; existing offline regression passes. |

The APPROVE verdict on `8850dc8` also included a nonblocking renderer coverage
note. The coverage guard now scans `news_impact.py` and verifies its rendered
fields rather than classifying them as deliberately unrendered.

## Verified Approval Inventory

Main ancestors with exact-commit APPROVE receipts:
`8850dc8`, `baffcc8`, `905cafd`, `0a5e228`, `609c577`, `5a20de8`,
`51b88bc`, `4b3b70f`, `31aa85f`, `98b03d8`, `d1657b5`, `6d074dd`,
`751deab`, `890ec66`, `5f16b5e`, `d622dcd`, `1cd687f`.

Branch-only APPROVE receipts: `d6eb958`, `de98abd`. These cannot be represented
as main-ancestor audit commits or used as a reason to merge unrelated branches.
Only true approvals may receive empty passed/high/Reviewed-Commit audit commits.

## Verification Boundaries

Repairs include offline regression tests, contract/version updates, existing
module-size ceilings, lint and boundary typing. Full candidate and main CI must
bind to the actual delivered SHA. No paid report-generation test, manual email,
production state rewrite, scoring coefficient change, or accuracy claim is part
of this repair. Live content acceptance waits for the ordinary scheduled email.
The original untracked research note is excluded from delivery.

The annotation fix was checked against the official
[GitHub runner command parser](https://github.com/actions/runner/blob/main/src/Runner.Common/ActionCommand.cs),
which requires the v2 command prefix at the start after leading whitespace.
