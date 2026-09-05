# Author-literature campaigns (local first version)

This mode reuses the existing Research, independent Problem Review and Compile
stages. It changes the input and discovery scheduler and adds incremental
same-author deduplication. Topic-based campaigns remain available unchanged.

## What the current seed-paper route actually does

In topic mode, `CampaignPipeline._discover_domain` includes `seed_papers` as
lookup hints in the Discovery prompt. The Discovery agent searches LKM using
Gaia and selects question IDs and paper identifiers. Deterministic ingestion
then calls `papers/graph` for the selected paper identifiers (paper ID, DOI,
then title as available), and takes the selected dedicated `open_questions`.
It is **not** a deterministic loop over the supplied seeds. Its source papers
can therefore differ from the configured seed list.

In author mode there is no Discovery selection agent:

1. Import `people[].literature_ids` and the corresponding `literature[]` records.
   Normalize DOI prefixes and case; consolidate shared papers into one work unit.
2. For each DOI, call LKM `papers/graph` directly with that DOI, using the existing
   `LKM_ACCESS_KEY`. Require a matching returned DOI. Do not substitute another
   paper or fall back to unrelated topic search.
3. Ingest **all** nonempty `data.papers[].open_questions[]` entries attached to
   that paper. There is no `questions_per_domain` limit in this mode; configuration
   containing that setting is rejected to avoid giving a false impression of a cap.
4. Research and independently review every candidate with the existing contracts.
   Later papers and web results may establish progress, but are not replacement
   sources for new candidates. Preserve the natural source problem's scope.
5. As each paper's audits finish, compare accepted records with the same author's
   already compiled problems, deduplicate, and compile new ones immediately.
   Do not wait for every paper by that author, or the whole campaign, to finish.

This is an LKM-indexed-open-question mode, not exhaustive full-text extraction.
A paper without an indexed open question produces no candidate in this version.
No primary-source location, quotation or source-relationship field is required.
Existing raw LKM text is retained as LKM evidence; it is not labeled as a verbatim
quotation from the paper. Primary-source checking during Research is unchanged.

## Generate and run a config

Run in Linux/WSL, as the existing pipeline uses POSIX process and file locks.
The commands below only use existing configured agent credentials; do not put
keys in the YAML or source list.

```sh
uv run discovery campaign init-authors \
  --literature-list /mnt/d/CommonQuestion/outputs/nobel-physics-fields-medal-laureate-literature.json \
  --author-id PER-0001 \
  --out work/author-pilot.yaml \
  --backend claude --model GLM-5.3 \
  --workers 32 --paper-workers 16 --candidate-workers 2

# Initialization above does not call LKM or an agent. Running below does.
uv run discovery campaign run work/author-pilot.yaml --run-id author-pilot-01
uv run discovery campaign resume work/work/runs/author-pilot-01
```

Repeat `--author-id` to include several authors; omit it to import all authors.
Every selected author's listed paper is included; no silent paper sampling or
candidate truncation is performed. Missing or parser-rejected DOI and missing
literature entries are retained in config `input_issues` with their literature
and author IDs. They are not scheduled or counted as empty results; the run
summary exposes them as `input_failed` and its status remains `partial_failed`.
Correct the input and initialize a new campaign with the same solution root to
add those papers without replaying completed papers. Unknown author IDs and
duplicate entity IDs fail initialization. Shared DOI records carry all selected
author and literature IDs. The importer never guesses a correction for a DOI.

Output paths are anchored to the config's directory. For a config in `work/`,
the default roots are therefore `work/work/runs` and `work/work/author-solutions`;
edit these paths before starting a run if desired. Never edit a frozen run config.
There is no deployment, GitLab upload, or change to running campaigns implied by
creating a config. Publication integration remains a separate authorized step.

## Concurrency and deduplication

For an explicitly requested end-to-end test with no agent-process deadline,
set `agents.timeout_seconds: null`. Defaults remain finite; this does not
disable HTTP/tool timeouts or repair CLI permissions. Monitor such runs because
an unresponsive model call will no longer be stopped by the stage wall clock.
For a strict single-Claude test, the executable may include verified CLI flags,
for example `claude --permission-mode acceptEdits --disallowedTools Agent,Task`.
This permits normal workspace edits without globally bypassing permissions and
disables built-in delegation. Validate a small workspace write before the test;
do not assume a process running as root bypasses Claude's own permission policy.

`--paper-workers 16` schedules up to 16 papers concurrently.
`--candidate-workers 2` runs up to two Research-to-Review chains per paper.
For each candidate, Review follows Research; they do not run simultaneously
against the same incomplete result. The campaign-wide limit of 32 also covers
model calls used for deduplication and direct LKM graph requests.

Deduplication first compares exact LKM source keys. Otherwise, an agent compares
only the incoming title with titles of same-author canonical records, in bounded
batches of 24. Exact normalized title matches are deterministic. Semantic title
matches are deliberately conservative: related topics, special cases and
extensions remain distinct, and uncertain matches are kept separate. No problem
statement, references, significance narrative, verification contract or other
full record is supplied to the title comparison. Decisions and reasons remain in
`candidates/*/dedup-*.json` and `author-provenance.json`. This title judgment is
model-based, not a proof of problem equivalence.

Only deduplication plus index/compile commits are serialized; independent
Research/Review work continues. One writer lock prevents two campaigns sharing
the same author index from making conflicting decisions. Identical paper tasks
across authors are retrieved once and retain all selected author associations.
Distinct authors' unrelated paper lists are not globally semantically deduplicated.

The persistent `problem_root/.author-literature-index.json` keeps canonical
problems, member candidate IDs, source DOIs, author IDs, and paper outcomes.
Later campaigns using the **same problem_root** reuse completed papers and compare
new records against previous author results. Do not mix it with a separately
edited solution inventory or delete it to restart a failed stage.

This first version compares all prior same-author problems and atomically writes
the index. Large author collections can make deduplication and index writes
expensive; run a small author pilot before scheduling the entire literature list.
Adding a new author does not retroactively merge two previously independent
authors' canonical repositories. Existing repository content is never replaced
merely because a later paper duplicates its problem; extra provenance goes into
the index, outside generated repositories.

## Outcomes and recovery

Every paper has `papers/<paper-task-id>/status.json` in its run directory.

| Status | Meaning | `campaign resume` |
| --- | --- | --- |
| `no_questions` | Matching LKM graph explicitly returned an empty question array | Reuses the valid empty snapshot |
| `discovery_failed` | Nonzero code (including 7002), timeout, malformed/missing graph, missing question array or unmatched DOI | Retries the lookup |
| `candidate_failed` | Some candidates failed Research/Review, deduplication or compilation | Reuses successful stage artifacts and retries failed work |
| `completed` | Every returned candidate reached accepted, duplicate or rejected outcome | Reuses the paper outcome |

`no_questions` does **not** mean that the paper has no scientific open problems.
Rejected candidates are distinct from empty discovery. A completed paper may
therefore have zero compiled repositories even though discovery returned candidates.
`partial_failed` is the campaign status when any paper remains failed.
Unexpected scheduler/index errors fail the run visibly, rather than marking success.

Failed raw responses are preserved per attempt (`graph-0001.json`, etc.) where
the server returned a JSON body. Every lookup also retains `attempt-*.json`,
including transport errors with no response body. A failed lookup never becomes a reusable empty
discovery artifact. Valid
graph snapshots are integrity-checked before reuse in the same run. Candidate
audits and compilation retain the original stage-ledger checkpoints.
Use `campaign resume`, not `compile-pending`: the latter is blocked in this mode
because it would bypass author-aware deduplication. To intentionally refresh a
previously successful empty graph after LKM changes, use a separate output root
for a new investigation; automatic refresh of successful empty snapshots is not
implemented in this first version.

## Input attribution and quality caveats

The author–paper associations are imported from the JSON, not independently
verified by the importer. Homonyms, incorrect matches, reviews and non-research
works need input-quality checking. Prize metadata is not evidence that a named
laureate wrote a particular paper, nor a significance score. Source records mark
author attribution as unverified; no source DOI is silently changed to repair an
attribution mismatch. The domain is initially grouped using the selected author's
award category (`mathematics` / `physics`), not a verified paper-level taxonomy.

Ordinary topic mode remains `discovery campaign init --topic ... --out ...`,
with both existing LKM and topic-search routes and their configured candidate cap.
## Bounded author scheduling

For four simultaneous primary authors with at most eight agent calls each,
set `agents.author_workers: 4`, `agents.agent_workers_per_author: 8`,
`agents.workers: 32`, `agents.topic_workers: 32`, and
`agents.candidate_workers_per_topic: 1`. Each author can then have eight paper
pipelines active; each paper's candidates pass sequentially through research
and review. Alternatively use 16 paper workers and two candidate workers per
paper: each author then has four active papers, each reserving two agent lanes.
The global worker semaphore still limits model calls across the campaign.

An author remains in the active set until all its paper tasks finish. The next
author then takes its place. Shared DOI papers are scheduled exactly once,
under the earliest author in the configured author list; every coauthor ID is
retained for provenance and incremental deduplication. Empty and failed paper
outcomes remain distinct and resumable as before. Omitting both new settings
preserves the previous paper-worker scheduler; topic mode is unchanged.
