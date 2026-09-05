# Author fleet operations

The September 2026 fleet is isolated at
`/root/open-research-discovery-author-fleet-20260905` on each host. It does not
replace the legacy topic repository or terminate legacy jobs.

`scripts/prepare_author_fleet.py` reads the accepted summary and its referenced
accepted-only literature file. All selected authors are retained. Authors linked
by a shared normalized DOI remain on the same host, then components are balanced
by paper count. This preserves author-local incremental deduplication without
duplicating a DOI across hosts. Invalid input is recorded, not converted into
an empty LKM result. The generated deployment manifest includes input hashes.

Each host has `workers: 32`, `author_workers: 4`,
`agent_workers_per_author: 8`, `topic_workers: 32`, and
`candidate_workers_per_topic: 1`. Four primary scheduling authors each have up
to eight concurrent paper chains. Research precedes independent review for each
candidate. LKM requests, incomplete author queues and serialized deduplication
can make actual active agent count lower than 32. There is no candidate cap.

## Retrieval and startup

The research skill is registered for Claude, and prompts also provide its exact
filesystem fallback. Explicit tool allowances avoid unattended approval waits;
built-in Agent/Task delegation is disabled. `opr-evidence` uses real HTTP search
results (Bing RSS or Crossref metadata) and public-source fetching instead of
treating model-only web-search prose as evidence. Native web tools can still
fail on some sites. Paywalls, missing literature and unavailable sources remain
limitations and must never be converted into `open` outcomes.

Claude print-mode prompts are transported over standard input rather than as a
single command-line argument. This avoids the Linux per-argument size limit.
Author deduplication nevertheless sends titles only; the transport fix is a
defensive guarantee, not permission to restore full-record semantic comparison.

`scripts/author_fleet_runtime.py preflight` exercises direct web and academic
search, primary-page fetching, direct LKM graph retrieval, an actual Claude Skill
invocation, Claude's LKM/search commands, a file write and GitLab authentication.
The runtime refuses campaign startup without a successful preflight result.
Secrets reside outside the repository in a root-only configuration directory.

## Services and publication

- `opr-author-fleet.service`: checkpointed campaign runner; no agent-process
  wall-clock deadline. Normal HTTP timeouts remain. Failed paper work may be
  retried at 15-minute intervals, with six automatic retries maximum. Successful
  checkpoints are reused. Invalid inputs require manual correction.
- `opr-author-publish.timer`: every two minutes, invokes the dedicated author
  adapter. It never runs `compile-pending` or edits campaign state.
- `opr-author-publish.service`: uses the existing GitLab publisher for namespace
  `peter0627ustc`. Only accepted, compiled records with `ready` in both the
  manifest and README are eligible. `uncertain`, resolved and rejected records
  are not automatically published.

The pipeline maps a reviewed `open` record to `ready` during compilation.
Publication identifiers are `MATH-AUTH-<20 hex characters>` and
`PHYS-AUTH-<20 hex characters>`, derived from the source DOI and LKM source keys.
They do not collide with legacy local ORP counters. A conflicting existing
remote README is not overwritten. Publication ledgers retain author IDs, DOI,
literature IDs and discipline under each run's `gewulab-publication` directory.

The new fleet root is separate from legacy `/root/open-research-discovery/work`.
Any future aggregate index collector must include the new root explicitly; an
old collector restricted to the legacy root will not see these publications.
