# ADR 0004 — Phase transition and evidence-gated tutor quality

Date: 2026-09-09\
Status: Adopted for documentation and planning; Phase 1 implementation pending\
Scope: Supersedes the Phase 0 active-objective wording, mandatory hybrid-retrieval target and
unimplemented parser/UI dependency prescriptions in architecture v1.0. Preserves ADRs 0001–0003
runtime contracts.

## Context

The project owner completed Increment 4 and merged `feature/walking-skeleton` into `develop`
at `e31d1aa`. The Phase 0 checklist and dated runbooks record the durable PDF-to-browser flow.
The owner requested a documentation transition based on two supplied planning discussions,
preserving history while moving the active work beyond the walking skeleton.

The code uses Spanish AND FTS over the original question, at most five chunks, a bounded
history for generation, and four response modes. It does not implement query resolution,
embeddings, hybrid retrieval or a structured pedagogical planner. Citation validation proves
provenance and exact quotation, not semantic support or learning effectiveness. Local model
smoke tests are insufficient to choose a pilot model.

The architecture mixed future choices with implementation claims: hybrid retrieval was marked
mandatory while evaluation sections required measured adoption. Frontend libraries and Docling
were prescribed although the completed slice uses assistant-ui with plain CSS and PyMuPDF.
The active roadmap and implementation inventory must make these distinctions explicit.

## Decision

1. Close Phase 0 as the technical walking skeleton. Preserve its path, checklists and dated
   evidence. Keep ADRs 0001–0003 as historical decisions with continuing runtime authority.
   Closure does not declare the product pilot-ready or establish pedagogical quality.
2. Make `docs/PHASE_1_TUTOR_QUALITY.md` the active scope and acceptance document, and
   `docs/CURRENT_STATE.md` the implementation inventory. Retain architecture as invariants and
   target design, with implemented refinements governed by the specific ADRs. Continue increment
   numbering from 5 for traceability; do not extend the Phase 0 backlog.
3. Implement evaluation first: 30–50 reviewed cases, an isolated offline runner over the real
   FTS baseline, versioned inputs and reports separating retrieval, answerability, grounding,
   pedagogy and operational measurements. Live model evaluation is explicit and separate from
   deterministic CI tests. Missing human review or unavailable metrics must remain visible.
4. Treat dense retrieval and FTS+dense+RRF as experimental candidates, not a mandatory pilot
   dependency. Define quality/cost/latency gates before running comparisons. Retain FTS if
   the candidates do not pass. Introducing pgvector still requires an implementation ADR,
   migrations, reproducible infrastructure and immutable index/version handling.
5. Preserve strict course grounding. Richer resolution and pedagogy use typed, versioned
   server-side decisions; do not depend on model chain-of-thought or allow agent loops.
   Future query rewriting must preserve the exact original message and its authorization
   binding. ADR 0002 remains in force until a specific query-contract decision refines it.
6. Plan a small `LearnerEvidence → PedagogicalDecision → RetrievalIntent → TutorResponse`
   contract, then measured conditioned retrieval and richer answerability. No such types or
   modes are claimed implemented by this ADR. A second retrieval is not a provider retry;
   any future bounded policy must preserve deadlines, evidence integrity and idempotency.
7. Keep the implemented PyMuPDF and plain-CSS/assistant-ui baseline. Docling, parent-child
   chunking and additional frontend libraries are deferred candidates, not migration duties.
   OpenUI remains optional and gated on stable pedagogical contracts and demonstrated UI need.
   General-knowledge fallback needs a separate product decision and is outside this plan.

## Alternatives and consequences

Continuing the skeleton would blur a completed technical proof with open quality questions.
Replacing the architecture or deleting old ADRs would lose rationale. Installing vectors or
changing the model immediately would make failures harder to attribute without a baseline.

This transition adds a reviewed dataset and evaluation cost before optimization. A 30–50-case
dataset is a diagnostic starting point, not statistically decisive evidence for every subgroup.
Later increments are a conditional sequence, not authorization to build all of them now. Pilot
readiness cannot depend on optional OpenUI or on adopting a candidate that fails its gate.

## Evidence, gates and reversal

The inventory links source and tests, and distinguishes recorded results from checks run during
the documentation task. The planning discussions supply hypotheses, not independently verified
paper findings or measured improvements. No runtime code, model configuration or database state
changes with this decision.

Increment 5 is complete only with a reproducible runner, reviewed cases and a baseline report;
draft/generated cases alone cannot close the dataset criterion. Subsequent choices must cite
their experiment and explicitly refine any affected accepted contract.

Revising this direction requires a new dated decision and an updated active plan. Preserve
Phase 0 evidence and evaluation versions even when a candidate is rejected; do not rewrite
historical reports or remove isolation/provenance guarantees to improve a score.

## Subsequent status note — 2026-09-22

Increment 5 remains pending in the inspected code. The documentation now distinguishes its
offline evaluation harness from the proposed pedagogical runtime. The imported architecture
recommends a bounded tool loop, which conflicts with decision 5 above; documenting that proposal
does not accept it. The active plan still preserves strict course grounding and the later
typed, bounded second-retrieval design without an agent loop.

A future acceptance must explicitly refine this ADR and affected parts of ADR 0002, with scope,
budgets, failure semantics, provider compatibility and evaluation gates. See the
[reconciled harness](../DOCTA_HARNESS_ARCHITECTURE.md) and [D-05](../DECISIONS.md).
This note records the distinction without altering the accepted decision or marking any
Phase 1 implementation complete.
