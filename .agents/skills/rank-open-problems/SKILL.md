---
name: rank-open-problems
description: Review open research problems for scientific significance, source fidelity, and optional internal verification difficulty in the discovery pipeline.
---

# Rank Open Problems

Judge scientific value and the clarity of the scientific objective, not how
easy the problem is to solve or retrieve.

## Role split

This policy is applied by stages with different output records:

- **Selection** (production) canonicalizes and *routes*. It reports the
  canonical formulation plus `importance_level` and a free-form `assessment`
  narrative. It does **not** output significance levels, verification
  contracts, difficulty, or CI.
- **Research** produces one Problem Schema v1.0 scientific record after the
  later-literature audit, plus `audit_outcome`. Its internal verification
  difficulty estimate is optional.
- **Problem Reviewer** checks the formulation and literature, and may retain
  or revise a supported internal difficulty estimate.

The decisions below apply to whichever stage produces the corresponding field.

## Required decisions

1. Judge scientific significance and assign
   `scientific_significance.affected_field.level` (high/medium/low) with a
   specific `description` (Research only). Explain which knowledge,
   capability, bound, mechanism, experiment, or decision would materially
   change. Also retain the coarse `importance_level` (high/medium/low), which
   is the only importance field Selection reports.
2. Research and Reviewer use the single shared difficulty prompt in
   `src/open_research_discovery/campaign.py` and
   `VERIFICATION_DIFFICULTY_RUBRIC` in `src/open_research_discovery/ranking.py`.
   Estimate residual independent-review burden for a hypothetical completed
   answer from the stated problem and inspected literature, not solving effort.
   Return only optional `verification_difficulty` with a 0-10 `score` and a
   short `rationale`; omit it or use null when unassessed.
3. Do not generate acceptance contracts, answer-type lists, CI plans, or
   verification documents. Do not undertake a separate investigation to design
   checking procedures, add requirements to lower the score, or weaken the
   scientific question into an easier proxy.

## Admission and ranking

High- and medium-importance Selection candidates proceed to later-literature
research — importance is the only selection gate. Publication requires an
`open` audited status and acceptance by the Problem Reviewer — an editing
review on a copy of the candidate folder, with LKM and web access, whose
corrected record is what gets compiled. A rejected
or non-open candidate is archived in the run directory, never re-issued or
decomposed into a queue. There is no maximum verification-difficulty
threshold. Rank first by current-open status and affected-field significance
level; use verification difficulty only as reviewer workload metadata.

## Output

- Selection returns the canonical candidates with `importance_level` and
  `assessment` (`schemas/stages/selection.schema.json`).
- Research returns a Problem Schema v1.0 record — title, abstract, background,
  references, previous_progress, problem_statement, scientific_significance,
  solution_difficulty — plus `audit_outcome` and optionally
  `verification_difficulty` (`schemas/problem.schema.json`).

Use [the casebook](../../../docs/verification-difficulty-casebook.md) for score
boundary examples. It calibrates reviewer burden; it is not a deterministic
artifact classifier.
