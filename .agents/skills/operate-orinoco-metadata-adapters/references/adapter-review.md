# Adapter decision and pull-request review

Use the repository's implemented schema and commands.
The fields below define the questions a robust adapter contract must answer; they are not a license to invent a new file format during site operation.

## Review candidate identity

Distinguish:

1. a source entity identifier;
2. a source snapshot or version;
3. a candidate canonical record;
4. an operational match used to find that candidate;
5. a human-reviewed identity or link decision;
6. an ontological mapping between semantic entities.

Do not collapse them into one fuzzy match or hash.
Prefer stable source-native identifiers and exact reviewed mappings.
If an identifier can be recycled, namespaced ambiguously, or change meaning, surface that limitation.

A durable proposal disposition normally needs the local equivalent of:

- source namespace and stable entity ID;
- candidate or claim kind;
- versioned semantic fingerprint over decision-relevant normalized fields;
- adapter matching or policy version;
- disposition and canonical target, when linked;
- reviewer, date, rationale, and evidence;
- invalidation or re-review rule;
- reference to a superseded decision.

The fingerprint must not be an adapter run ID.
Exclude volatile retrieval dates, irrelevant ordering, and display-only changes.
A material source or policy change should normally make a prior rejection stale and return it to review.
Use a separate explicit scope for a permanent entity-level suppression.

## Guide the human without deciding

For each unresolved item, present:

- the decision in one sentence;
- exact source and canonical candidates;
- decisive evidence and conflicting evidence;
- effects of accept, reject, link, defer, or supersede;
- a labeled recommendation and its assumptions;
- the exact local files and assertions the choice will govern.

Do not combine independent identity, eligibility, semantic, and publication questions into one approval.
Do not record a recommendation as the reviewer's choice.
Follow local attribution rules and preserve who actually authored each decision or machine change.

Treat absent policy as pending.
Treat stale link targets, duplicate decision keys, contradictory decisions, and expected-but-unused policy as errors or visible review findings, not as reasons to guess.

## Apply accepted provenance correctly

Git or DataLad records the execution.
PAV or the supported Things statement shape records the origin of accepted assertions.
These are complementary.

Use the exact schema and helper version selected by the site's lock.
Upstream may add, remove, or reinterpret helper behavior after that pin.
Compare source and tests before adopting newer annotations.

Do not attach a rejection to a nonexistent Thing merely to make it public.
Do not use a false SKOS or SSSOM semantic mapping, SSSOM `NoTermFound`, or a negated mapping as a workflow rejection flag.
Keep the human disposition in site-owned curation state unless the site has explicitly adopted a public decision model.

If a semantic mapping is accepted site knowledge, record it in the locally declared canonical representation.
Do not maintain independent Things and SSSOM copies by hand.

## Verify future-run behavior

Run or inspect tests proving:

- the same base, source, policy, and decisions produce no new diff;
- an unchanged rejected candidate is not proposed again;
- a materially changed candidate returns as stale for review;
- a policy or matching version change follows an explicit invalidation rule;
- missing decisions are not treated as rejection;
- ambiguous matches remain unresolved;
- stale and unused decision entries fail visibly;
- deleting ignored caches cannot erase decisions;
- accepted assertion provenance survives projection;
- an all-rejected run retains decision state without creating a Thing.

If the current adapter cannot pass a relevant case, report the gap.
Do not claim the static decision-memory extension is implemented merely because this skill describes the desired behavior.

## Keep the pull request self-consistent

Prefer this sequence on one branch when local policy permits:

1. propose from the declared snapshot and base;
2. commit or otherwise capture the reviewable proposal;
3. record explicit human decisions;
4. apply the decisions with a recorded command;
5. validate the final tree and rerun to prove idempotence;
6. obtain human review of the latest head.

If reviewers cannot write the branch, a bot may transport an already explicit decision into a follow-up commit or pull request.
The bot is optional workflow plumbing, not an authority.
Merge durable decisions before another adapter run is expected to rely on them.
