# Source-backed longitudinal morning research

Approved product shape (2026-09-06): context for important articles, at most
three deeper daily themes, and Sunday synthesis across weeks. Existing email
sections, compact mobile typography, and Python investment authority remain.

## Architecture references and bounded adoption

- [Graphiti](https://github.com/getzep/graphiti): distinguish publication from
  first observation, retain source revisions and provenance. No graph database
  or hosted memory service is installed.
- [Microsoft GraphRAG](https://microsoft.github.io/graphrag/query/overview/):
  combine event-focused retrieval with weekly theme synthesis. The existing
  deterministic event scorer is reused; its weights are unchanged.
- [Stanford STORM](https://github.com/stanford-oval/storm): perspective-driven
  questions (execution, counterparties, regulation, counterevidence).
- [Open Deep Research](https://github.com/langchain-ai/open_deep_research):
  separate bounded research from writing, with explicit evidence gaps. This
  implementation adds no model calls or agent runtime of its own; the existing
  writer and bounded depth-repair loop consume the enriched evidence.
- GDELT was considered as an additional discovery source, not installed. Adding
  a second index requires live API availability and incremental coverage tests;
  article count alone does not establish editorial independence.

## Six directions and concrete mechanisms

1. Breadth: source/content-level and sector coverage at ingestion, deduplication,
   packet selection and analyzed output. Supplementary queries cover up to three
   current themes and two rotating quiet historical subjects. Second fulltext
   choices favor a different known editorial group. Unknown sources are never
   silently counted as independent confirmations.
2. Memory: `news_memory.py` stores allowlisted original-source observations in
   daily gzip partitions under `NEWS_MEMORY_DIR`. No generated views, portfolio
   objects, or investment scores are archived. Existing atomic writer and state
   publish registry own durability. A corrupt partition prevents reconstruction
   over the existing file. No partition is automatically deleted.
3. Retrieval: entity/action/incident-compatible historical sources, excluding
   self-comparison. Preserve origin plus recent observations (up to six), disclose
   omissions, and allocate a 36,000-character historical-source budget round-robin.
   An observation not known at as-of cannot enter a historical replay. This is
   conservative retrieval, not an authoritative event-identity migration.
4. Research: at most five supplemental Google RSS queries, at most four matching
   articles per query. A 75-second soft launch budget plus delivery reserve
   prevents starting further work; individual HTTP requests retain the existing
   timeout/retry/circuit breaker. No claim of a hard wall-clock deadline is made.
   The query pattern was HTTP-tested on 2026-09-06: 200, 29 RSS items for a public
   semiconductor example. Production still validates dates, subjects and URLs.
5. Impact: existing mechanism stages, fact/inference/unknown distinctions,
   magnitude limits, confirmation and invalidation signals are used for the
   Python-selected daily themes. Historical context has dedicated source IDs;
   historical reports cannot alone support current-direction inference. A
  compact source-linked fallback provides prior reporting when the writer omits
  synthesis, without pretending that fallback is deeper reasoning.
   Source URLs count toward Markdown size: a measured 532-character RSS link
   shape produced a legitimate full report above the old 40k fuse while staying
   within the 36k source budget. A 70k fuse retains the tail sections and keeps
   runaway truncation observable; a full-capacity regression test enforces this.
6. Evaluation: structural longitudinal metrics enter existing quality metrics;
   the read-only `tools/evaluate_news_research.py` audits as-of retrieval using
   caller-supplied historical snapshots. It does not label citation existence as
   semantic correctness. Existing watch-review tracks validation conditions.

## Isolation, rollout and outstanding acceptance

- Supplemental research sources are not appended to prediction/scoring inputs.
  Fresh reports enter the writer's source pool; older discoveries are explicitly
  dated background. Learning an old report today never backdates first knowledge.
- Historical IDs are confined to each article's matched `historical_context`;
  other inference fields require current evidence, including mixed citations.
  Canonical feed `published_dt` retains valid updated-only feed timestamps.
- Unreadable old partitions are preserved and visibly excluded during ingestion;
  today's independent partition can still accumulate. An unreadable current
  partition blocks writing; offline audits and state validation remain strict.
- Both structured and legacy prompts sanitize external strings and put data in
  non-nested untrusted fences; rules remain outside. Research diagnostics and
  editorial configuration are not reader-facing text.
- Sunday reads source partitions without network calls, selects this week's
  themes and supplies older matched evidence. Legacy title-only history remains
  available during cold start. State starts accumulating only after deployment;
  previously missing raw evidence is not manufactured from old model output.
  Weekly material retains complete URLs up to 2048 characters; longer links are
  explicitly unavailable, never shortened into broken links. A 36k-character
  source budget retains all selected current themes, then fairly allocates
  preceding reports and records omissions instead of silently cutting URLs.
- Offline tests and structural counters do not establish production writing
  quality. Candidate CI and a non-sending, isolated-state pipeline dry-run must
  still verify the delivered SHA, actual provider path, HTML and run diagnostics.
  A multi-day as-of comparison must explicitly report archive gaps and requires
  human source-level review of causality, corrections and material omissions.
- No production SMTP, official state mutation, subscription change, new runtime
  dependency, scoring coefficient change, or automatic model substitution is
  part of the development-time verification.

## Full production-path canary

Manual CI now runs `tools/preview_morning_report.py --kind full` with `DRY_RUN=1`.
Before importing the production module it copies checked-in historical state to
a fresh temporary directory, excluding the stale manifest. It runs the existing
production phase list with actual time/provider settings, including on Sundays;
scheduled Sunday dispatch itself is unchanged. SMTP, state push and atomic writes
outside the temporary state tree are explicitly blocked. Strict acceptance binds
the fresh manifest to the same SHA, Actions run and nonce; HTML and manifest are
uploaded as evidence. The optional local `--kind scheduled` preview follows actual
calendar dispatch and is not evidence of weekday specialized-analysis success.

The first live canary exposed two acceptance gaps: the manifest writer serialized
absent delivery as null, and the model omitted all three planned deep topics.
Absent delivery now stays absent before SMTP (explicit invalid records still
fail). Selected deep themes must appear as nonempty current-source analysis or
have an evidence-backed dismissal under the existing coverage contract, with a
concrete reason and revisit trigger. Silent omissions enter the existing bounded
repair path, without extra model calls or fabricated depth. Dismissed themes are
counted separately, never as analyzed. Coverage is not a semantic quality score.
