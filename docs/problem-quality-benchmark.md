# Problem-quality benchmark

The problem-quality benchmark measures the *end-to-end quality of a published
problem repository*: is the complete solver-visible README actually
trustworthy, given the available citation evidence?

Use it only as an explicit audit workflow. Ordinary problem generation runs
`discovery campaign`.

## Evaluation target

The evaluation object is the **solver-visible README**. Read its statement,
background, definitions, and current-progress context together, distinguishing
background results and intermediate milestones from the final objective.
The internal problem.schema record is retained for provenance and mechanical
validation, but only its references and previous-progress notes are passed to
the reviewer as supporting audit context. Neither those notes nor frozen
evidence may repair missing public definitions, assumptions, quantifiers, or
completion conditions, or impose additional requirements. Internal audit
assertions are not proof of source support; bibliographic metadata alone does
not establish a paper's formulation or its current solution status. If the
README is unavailable, report missing public input rather than reconstructing
it from the internal record. Each case is scored on
five dimensions, each an integer from 0 (fundamentally broken) to 3 (sound),
with concrete issues (`type`/`severity`/`detail`) and an overall grade:

1. **citation_accuracy** — cited works exist and are paraphrased accurately
   when cross-checked against the frozen citation metadata.
2. **openness_argument** — public openness claims and the current-progress
   narrative are supported by available cited evidence, not merely asserted
   or supplied by hidden audit notes.
3. **scope_fidelity** — the statement does not silently narrow or drift, its
   requested results form a coherent scientific objective, and any
   named-problem or lineage narrative in `background` and `previous_progress`
   is truthful. Several linked methods, deliverables, or potential papers are
   not a defect by themselves; relative domain language is acceptable when
   sources, context, or established field conventions make it sufficiently
   determinate for a qualified expert.
4. **verification_executability** — a qualified domain expert can judge a
   hypothetical completed answer against the complete public objective without inventing
   scope, circularly assuming the conclusion, ignoring an unrelated requested
   result, or accepting a proxy or materially narrower target. Holistic proof
   review, scientific judgment, and independent replication are valid forms
   of assessment. No acceptance contracts, answer-type lists, prescribed
   checking procedures, CI, or verification documents are required. Their
   absence and a missing or high internal verification-difficulty score are
   not quality defects. Conditions actually written in the README remain
   public requirements, but hidden conditions cannot repair or add to them.
   Evaluation does not solve the problem or design a checker. The existing
   dimension name and 0–3 scale remain unchanged for JSON compatibility;
   this is not the internal 0–10 verification-difficulty estimate.
5. **evidence_relevance** — each evidence item genuinely bears on this
   problem; Direct/Adjacent-style framing is not inflated.

Grades: **A** = publishable as-is (no major issues); **B** = sound core with
minor revisions; **C** = major defects. A separate deterministic layer
(mechanical checks, below) catches defect classes that need no judgment.

## Building a dataset (networked, once per version)

```bash
uv run discovery quality build --run-dir /path/to/campaign-run --out quality-v1
uv run discovery quality build --pool /path/to/problem-pool --out quality-v1
uv run discovery quality build --manifest /path/to/manifest-or-dir --out quality-v1
```

The three sources compose. `--pool` reads the unified `catalog.jsonl`, so
active (`pool/problems/`) and settled (`pool/resolved/`) snapshots are both
collected via each record's `snapshot` path. Build collects every manifest,
validates it
against `schemas/problem.schema.json` (failures are kept as flagged invalid
cases rather than dropped), and **freezes citation evidence**: every DOI,
arXiv ID, or URL found in the manifest's `references[]` and
`previous_progress[]` strings
is classified (arXiv id, DOI, or bare URL) and
resolved programmatically — arXiv via `export.arxiv.org/api/query`, DOI via
`api.crossref.org/works/<doi>`, bare URLs fetched for their HTML title. The
result (`status: found|not_found|error|skipped`, fetched-at timestamp, and
title/authors/venue/year/doi/url metadata) is stored in the case's
`frozen_evidence`. A disk cache (`--cache-dir`, default
`<out>/.evidence-cache`) prevents refetching an identifier, and network
failures are recorded as `error` entries instead of aborting the build.
`--offline` skips fetching entirely (serving only the cache); the evaluation
loop itself never needs the network.

`--inputs-only` marks the exported dataset as pending manual labeling.

An evidence-fetch `error` or `skipped` status is an availability limitation,
not a negative claim about the cited work. The blind evaluator may report that
coverage is unavailable, but must not lower a quality score solely for that
status; only `not_found` or conflicting fetched metadata is citation evidence.

Each case's `input.json` contains the manifest, the README markdown (when
locatable — from the campaign-recorded problem repository for `--run-dir`),
the frozen evidence, provenance, and a build-time scoring-rubric snapshot. It contains no
pipeline verdicts beyond the schema-validity flag, and that flag is withheld
from the evaluated agent.

## Evaluating (offline, repeatable)

```bash
uv run discovery quality validate quality-v1 --inputs-only

uv run discovery quality evaluate quality-v1 \
  --out quality-eval-run \
  --backend codex --workers 3

uv run discovery quality evaluate quality-v1 \
  --out quality-eval-run --resume
```

`evaluate` runs one ephemeral headless reviewer per case with `read-only`
sandboxing and `network_access=false`. The prompt
contains only the README, supporting references and previous-progress notes,
the frozen evidence, and the current canonical rubric —
no pipeline context, so the reviewer cannot self-confirm the pipeline's own
judgments. Citation cross-checks must rely exclusively on `frozen_evidence`.
Legacy datasets remain readable, but their stored rubric cannot reintroduce
superseded contract-based requirements: evaluation uses the current
`QUALITY_RUBRIC`. `--resume` reuses a schema-valid prediction only when its
recorded input, evaluation-prompt, and output-schema hashes still match.
Predictions produced before this policy change (or without those hashes) are
re-evaluated; the frozen dataset and existing expert gold labels are not rewritten.

## Scoring (deterministic, no agent)

```bash
# Standalone report (no gold labels yet):
uv run discovery quality score --dataset quality-v1 \
  --predictions quality-eval-run/predictions \
  --out quality-eval-run/report.json

# Against expert blind gold labels under gold/:
uv run discovery quality score --dataset quality-v1 \
  --predictions quality-eval-run/predictions --gold quality-v1/gold
```

Mechanical checks always run against the dataset, with or without
predictions:

- **schema validity** — a manifest that failed `problem.schema.json`
  validation is flagged `invalid_manifest` but stays in the dataset.
- **hallucination counting** — identifiers whose frozen status is
  `not_found` are critical defects and feed the hallucination rate.
- **fetch errors** — identifiers whose frozen status is `error` are recorded
  as minor `evidence_fetch_error` issues.
- **README layout** — the public README is checked with the same
  `validate_problem_readme` rules used for published repositories; a case
  without a locatable README is flagged `missing_readme`.
- **cross-case duplicates** — problem statements with normalized token
  Jaccard similarity above 0.8 are flagged `duplicate_suspect` (the
  ORP-0002/ORP-0004 failure mode).

With gold labels (`schemas/quality/gold.schema.json`: same five dimensions
plus an overall grade and notes), the report adds per-dimension exact
accuracy and MAE plus overall-grade accuracy. Without gold it is a
standalone report: per-case dimension scores and issues, the mechanical
findings, and aggregate metrics — hallucination rate,
duplicate-suspect rate, mean dimension scores, and grade distribution. The
same agent output cannot serve as both prediction and gold.

## Layout

```text
schemas/quality/input.schema.json       frozen case: manifest + README + frozen_evidence
schemas/quality/prediction.schema.json  reviewer output: 5 dimensions + issues + grade
schemas/quality/gold.schema.json        expert blind labels: same dimensions + notes
src/open_research_discovery/quality.py  build / validate / evaluate / score + fetcher
tests/test_quality_benchmark.py         fully offline; fake fetcher and fake runner
```
